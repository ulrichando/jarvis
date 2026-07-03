"""Thinking / tool-busy tray-flag heartbeat.

Extracted from jarvis_agent.py 2026-07-03 (advisor-plan 006, wave 1). Owns the
two flag files the voice-client polls for the desktop tray "thinking" amber
(~/.jarvis/.agent-thinking + ~/.jarvis/.tool-running) and the heartbeat that
keeps the thinking flag fresh across long turns, with idle + orphan backstops.
Pure side-effect file I/O + asyncio task management on the session; no coupling
to the rest of the agent. The per-turn tool-call chain counter that used to sit
alongside these stayed in jarvis_agent.py (it belongs with run_jarvis_cli).
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger("jarvis.thinking_heartbeat")


# Tool-busy flag file. Tools write a small token file at start and
# remove it at end; the voice-client polls its mtime + presence on
# /status so the desktop tray can show "thinking" amber for the
# full duration of a long-running tool call (run_jarvis_cli can
# take 10-15 s; without this signal the inferred-thinking TTL gives
# up after 12 s and the tray flickers back to green even though
# JARVIS is still working).
_TOOL_BUSY_FILE = Path.home() / ".jarvis" / ".tool-running"


def _mark_tool_start(name: str) -> None:
    try:
        _TOOL_BUSY_FILE.parent.mkdir(parents=True, exist_ok=True)
        _TOOL_BUSY_FILE.write_text(f"{name}\n{int(time.time())}\n", encoding="utf-8")
    except Exception as _e:
        # Tray-busy indicator file write — non-fatal; tray will fall
        # back to inferred-thinking detection. Log at DEBUG so a real
        # FS / permission bug is still observable when needed.
        logger.debug(f"[tool-busy] write failed: {_e}")


def _mark_tool_end() -> None:
    try:
        _TOOL_BUSY_FILE.unlink(missing_ok=True)
    except Exception as _e:
        logger.debug(f"[tool-busy] unlink failed: {_e}")


# Definitive "agent is thinking" signal. Touched the moment STT
# finalizes a user turn (= LLM is about to start generating), removed
# when the assistant turn is committed (= TTS already played, agent's
# done). Replaces the desktop's prior heuristic of inferring thinking
# from listening→quiet transitions, which had a false-positive on
# every ambient mic trigger that VAD picked up.
_AGENT_THINKING_FILE = Path.home() / ".jarvis" / ".agent-thinking"


def _mark_thinking_start() -> None:
    try:
        _AGENT_THINKING_FILE.parent.mkdir(parents=True, exist_ok=True)
        _AGENT_THINKING_FILE.write_text(
            str(int(time.time())), encoding="utf-8",
        )
    except Exception as _e:
        logger.debug(f"[agent-thinking] write failed: {_e}")


def _mark_thinking_end() -> None:
    try:
        _AGENT_THINKING_FILE.unlink(missing_ok=True)
    except Exception:
        pass


# Heartbeat-driven thinking-indicator (2026-05-27). Replaces the
# agent_state_changed-driven file management which broke during long
# turns: the framework transitioned through "listening" or "speaking"
# between tool calls, the file got unlinked, indicator went green
# while JARVIS was actively reviewing/researching for the user.
#
# The heartbeat task starts on user_input_transcribed(is_final=True)
# and runs until the assistant emits a FINAL reply (text content, no
# tool_use) or until the turn is interrupted/cancelled. While running,
# it re-touches _AGENT_THINKING_FILE every `interval_s` seconds — the
# desktop's 60s TTL becomes a generous floor instead of the operative
# expiry.
async def _thinking_heartbeat(interval_s: float = 3.0, *, session=None) -> None:
    """Touch _AGENT_THINKING_FILE every `interval_s` seconds.

    On cancellation, unlinks the file so the desktop indicator goes
    green immediately. Idempotent: external unlinks are repaired on
    the next tick.

    Orphan watchdog (2026-05-30): when `session` is given, the heartbeat
    ALSO self-cancels if no genuine turn progress (`_bump_turn_activity`:
    user input / tool batch / assistant reply) has landed for
    `_thinking_max_idle_s()` AND no tool is running. This is the
    agent_state-INDEPENDENT backstop: the idle/listening cancel
    (`_schedule_idle_heartbeat_cancel`) only fires when the framework
    cleanly transitions to idle, but a turn can wedge agent_state at
    "speaking"/"thinking" (live 2026-05-30: a non-interruptible TTS whose
    playout never completed left the heartbeat orphaned for minutes). The
    tool-busy guard keeps a long `run_jarvis_cli` from clearing early."""
    max_idle = _thinking_max_idle_s()
    try:
        while True:
            if session is not None:
                last = getattr(session, "_jarvis_last_turn_activity", None)
                if (last is not None
                        and (time.monotonic() - last) > max_idle
                        and not _TOOL_BUSY_FILE.exists()):
                    logger.info(
                        f"[heartbeat] self-cancelled: no turn progress for "
                        f"{max_idle:.0f}s, no tool running (orphan guard)"
                    )
                    _mark_thinking_end()
                    return
            _mark_thinking_start()
            await asyncio.sleep(interval_s)
    except asyncio.CancelledError:
        _mark_thinking_end()
        raise


def _start_thinking_heartbeat(session, interval_s: float = 3.0) -> None:
    """Start (or restart) the heartbeat task on this session. Any prior
    task is cancelled defensively — handles back-to-back user inputs
    that arrive faster than the previous turn-end."""
    prior = getattr(session, "_jarvis_thinking_heartbeat", None)
    if prior is not None and not prior.done():
        prior.cancel()
    _bump_turn_activity(session)  # fresh progress clock for the new turn
    try:
        session._jarvis_thinking_heartbeat = asyncio.create_task(
            _thinking_heartbeat(interval_s=interval_s, session=session)
        )
    except Exception as _e:
        logger.debug(f"[heartbeat] start failed: {_e}")
        session._jarvis_thinking_heartbeat = None


def _cancel_thinking_heartbeat(session) -> None:
    """Cancel the heartbeat task on this session if running. Idempotent."""
    task = getattr(session, "_jarvis_thinking_heartbeat", None)
    if task is None:
        return
    if not task.done():
        task.cancel()
    session._jarvis_thinking_heartbeat = None


# Grace before the agent_state-idle backstop cancels the thinking
# heartbeat (see _on_agent_state). The normal cancel lives in _on_item
# (final-reply detection), but a turn can end with NO final assistant
# item — e.g. the framework logs "skipping reply to user input, current
# speech generation cannot be interrupted" (live 2026-05-30) — and then
# _on_item never fires, so the heartbeat keeps re-touching the flag every
# 3s and the tray's amber "thinking" sticks forever. If the agent settles
# into idle/listening and STAYS there this long, the turn is truly over.
# Generous enough to ignore the framework's transient sub-second
# "listening" between tool calls; short enough that a leak self-heals.
def _thinking_idle_grace_s() -> float:
    try:
        v = float(os.environ.get("JARVIS_THINKING_IDLE_GRACE_S", "5.0"))
        return v if v > 0 else 5.0
    except (TypeError, ValueError):
        return 5.0


# Hard ceiling for the heartbeat's orphan watchdog (see _thinking_heartbeat):
# if a turn produces NO progress (_bump_turn_activity) for this long and no
# tool is running, the heartbeat self-cancels even if agent_state never went
# idle. Generous so it rarely clears during a long legit turn; the fast path
# for normal turns is the 5s idle backstop. Bounds a wedged-state leak to this
# instead of forever.
#   CAVEAT: the tool-busy guard (~/.jarvis/.tool-running) only covers
#   `run_jarvis_cli` — that's the only tool calling `_mark_tool_start`. A
#   `computer_use` / `dispatch_agent` call that runs past this ceiling emits no
#   interim agent-side event and sets no tool-busy flag, so the watchdog WILL
#   fire mid-turn and flip the indicator green while JARVIS is still working
#   (cosmetic; self-heals on the next real event). FOLLOW-UP: have those two
#   tools call `_mark_tool_start`/`_mark_tool_end` to close this gap.
def _thinking_max_idle_s() -> float:
    try:
        v = float(os.environ.get("JARVIS_THINKING_MAX_IDLE_S", "120.0"))
        return v if v > 0 else 120.0
    except (TypeError, ValueError):
        return 120.0


def _bump_turn_activity(session) -> None:
    """Record genuine turn progress for the heartbeat's orphan watchdog.
    Called on user input, tool-batch execution, and assistant replies —
    NOT on raw agent_state changes (which can flap during a wedge and keep
    a dead turn's heartbeat alive). Idempotent / failure-silent."""
    try:
        session._jarvis_last_turn_activity = time.monotonic()
    except Exception:
        pass


def _schedule_idle_heartbeat_cancel(session) -> None:
    """Backstop cancel for the thinking heartbeat. If the agent settles
    into idle/listening and STAYS there past `_thinking_idle_grace_s()`,
    the turn is over — cancel the heartbeat so the tray stops showing
    amber. A return to thinking/speaking aborts the pending task via
    `_cancel_pending_idle_heartbeat_cancel`. Covers turns that end with no
    final assistant item (the framework skips the reply when the current
    speech can't be interrupted), which `_on_item` never sees. Idempotent:
    no-op if the heartbeat isn't running or a cancel is already pending."""
    hb = getattr(session, "_jarvis_thinking_heartbeat", None)
    if hb is None or hb.done():
        return
    prior = getattr(session, "_jarvis_thinking_idle_cancel_task", None)
    if prior is not None and not prior.done():
        return

    async def _idle_cancel(_sess=session):
        try:
            await asyncio.sleep(_thinking_idle_grace_s())
            if getattr(_sess, "agent_state", "") in ("idle", "listening"):
                _cancel_thinking_heartbeat(_sess)
                logger.info(
                    "[heartbeat] cancelled after sustained idle "
                    "(turn ended with no final assistant reply)"
                )
        except asyncio.CancelledError:
            pass

    try:
        session._jarvis_thinking_idle_cancel_task = asyncio.create_task(_idle_cancel())
    except Exception as _e:
        logger.debug(f"[heartbeat] idle-cancel schedule skipped: {_e}")


def _cancel_pending_idle_heartbeat_cancel(session) -> None:
    """Abort a pending idle backstop-cancel — the turn resumed
    (thinking/speaking), so the heartbeat must keep running. Idempotent."""
    t = getattr(session, "_jarvis_thinking_idle_cancel_task", None)
    if t is not None and not t.done():
        t.cancel()
    session._jarvis_thinking_idle_cancel_task = None
