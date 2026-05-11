"""Loop + agent-presence + stale-STT watchdog for the voice client.

Three layered detectors keep the voice-client process resilient:

  1. **Asyncio loop watchdog** — OS thread polls a heartbeat
     timestamp set by an in-loop task. If the timestamp goes stale
     by `WATCHDOG_STALE_SEC`, the thread `os._exit(1)`s so systemd
     Restart=on-failure brings up a fresh process. Catches loop
     wedges (sync-over-async, GIL-held C extensions, etc.).

  2. **Agent-presence watchdog** — if we're connected to the SFU
     but `state.agent_present` stays False past
     `AGENT_DISPATCH_TIMEOUT_SEC`, the agent worker missed its
     dispatch — restart ourselves to force a fresh dispatch.

  3. **Stale-STT watchdog** — detects a dead Groq STT connection
     (TCP CLOSE-WAIT): voice activity was recent, but
     `conversations.db` hasn't been updated since the voice ended.
     Restarts the agent unit to drop the dead socket.

State is encapsulated in the `LoopWatchdog` class so the watchdog
doesn't rely on module-level globals (which the previous inline
version did, making testing + reasoning harder).

Hoisted from `jarvis_voice_client.py` 2026-05-10 (Step 7 of the
audit).
"""
from __future__ import annotations

import asyncio
import faulthandler
import logging
import os
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Callable


__all__ = [
    # Constants
    "WATCHDOG_HEARTBEAT_SEC",
    "WATCHDOG_POLL_SEC",
    "WATCHDOG_STALE_SEC",
    "AGENT_DISPATCH_TIMEOUT_SEC",
    "STALE_STT_SEC",
    # Watchdog class
    "LoopWatchdog",
]


# Asyncio loop heartbeat — how often the in-loop task stamps the
# shared timestamp, and how long until the OS thread declares
# "stalled" and kills the process.
WATCHDOG_HEARTBEAT_SEC: float = 5.0
WATCHDOG_POLL_SEC: float      = 10.0
WATCHDOG_STALE_SEC: float     = 60.0

# How long to wait for `state.agent_present == True` before
# assuming the agent worker missed the dispatch (race: client
# connected before worker registered).
#
# 2026-05-02: lowered 45s → 10s. Production voice products (Vapi,
# Retell, Pipecat) use sub-15-second dispatch timeouts. 45s of
# silence is unusable in a voice loop.
AGENT_DISPATCH_TIMEOUT_SEC: float = 10.0

# How long after voice activity with no DB update before we declare
# the Groq STT connection dead and restart both services. 4 minutes
# is long enough to cover a legitimate long tool call (those update
# the DB mid-run) but short enough that the user doesn't wait half
# an hour before JARVIS self-heals.
STALE_STT_SEC: float = 4 * 60.0


class LoopWatchdog:
    """Encapsulates the three watchdog layers + their shared state.

    Construct once at startup, after the `state: ClientState`
    instance is available:

        watchdog = LoopWatchdog(
            state=state, log=log,
            restart_agent_unit=_restart_agent_unit,
        )

    Then in main() after `loop = asyncio.get_running_loop()`:

        watchdog.start_os_thread(loop)
        asyncio.create_task(watchdog.heartbeat_loop(shutdown))
        asyncio.create_task(watchdog.agent_presence_watchdog(shutdown))
        asyncio.create_task(watchdog.stale_stt_watchdog(shutdown))

    And the room-event handler that detects local voice activity:

        @room.on("active_speakers_changed")
        def _on_speakers(speakers) -> None:
            if any(p.identity == IDENTITY for p in speakers):
                watchdog.mark_voice_active()
    """

    def __init__(
        self,
        *,
        state: Any,
        log: logging.Logger,
        restart_agent_unit: Callable[[], Any],
    ) -> None:
        self.state = state
        self.log = log
        self.restart_agent_unit = restart_agent_unit

        # Asyncio-loop heartbeat — written by `heartbeat_loop`, read
        # by `_watchdog_thread` under `_heartbeat_lock`.
        self._last_heartbeat: float = time.monotonic()
        self._heartbeat_lock = threading.Lock()

        # Local-voice-activity timestamp — stamped by
        # `mark_voice_active()` from the LiveKit
        # `active_speakers_changed` event. Read by `_check_stale_stt`.
        # Both happen on the asyncio loop, no lock needed.
        self._last_voice_active_ts: float = 0.0

        # Captured by `start_os_thread()` so the OS-thread watchdog
        # can ask the asyncio loop for its task list at the moment of
        # stall. Without this, asyncio.all_tasks() defaults to the
        # current thread's running loop — which is None inside the
        # watchdog thread.
        self._main_loop: asyncio.AbstractEventLoop | None = None

    # ── External hook (called from room event handler) ──────────────

    def mark_voice_active(self) -> None:
        """Called when the local participant becomes an active speaker.
        Sets the timestamp the stale-STT watchdog reads to detect a
        dead Groq STT connection."""
        self._last_voice_active_ts = time.time()

    # ── Asyncio loop heartbeat ──────────────────────────────────────

    async def heartbeat_loop(self, shutdown: asyncio.Event) -> None:
        """Asyncio task: stamps the shared timestamp every few seconds.
        The watchdog OS thread checks this timestamp; if it goes stale,
        it kills the process."""
        while not shutdown.is_set():
            with self._heartbeat_lock:
                self._last_heartbeat = time.monotonic()
            try:
                await asyncio.wait_for(shutdown.wait(), timeout=WATCHDOG_HEARTBEAT_SEC)
            except asyncio.TimeoutError:
                pass

    # ── Agent-presence watchdog ─────────────────────────────────────

    async def agent_presence_watchdog(self, shutdown: asyncio.Event) -> None:
        """If we're connected but agent_present stays False for too long,
        the SFU never dispatched a job (timing race between agent restart
        and our room connection). Restart ourselves to force a fresh
        dispatch."""
        # Give a grace window from startup — the SFU can take a few
        # seconds to route the job even under normal conditions.
        await asyncio.sleep(AGENT_DISPATCH_TIMEOUT_SEC)
        while not shutdown.is_set():
            if self.state.connected and not self.state.agent_present:
                self.log.warning(
                    f"[presence-watchdog] connected but no agent after "
                    f"{AGENT_DISPATCH_TIMEOUT_SEC:.0f}s — restarting to force dispatch"
                )
                try:
                    await asyncio.create_subprocess_exec(
                        "systemctl", "--user", "restart", "jarvis-voice-client",
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                except Exception as e:
                    self.log.warning(f"[presence-watchdog] restart failed: {e}")
                return
            try:
                await asyncio.wait_for(shutdown.wait(), timeout=15.0)
            except asyncio.TimeoutError:
                pass

    # ── Stale-STT watchdog ──────────────────────────────────────────

    async def stale_stt_watchdog(self, shutdown: asyncio.Event) -> None:
        """Detect and self-heal a dead Groq STT connection.

        Failure mode: after several hours the HTTPS socket to Groq
        enters CLOSE-WAIT — the agent appears healthy (connected,
        agent_present) but audio frames go into a dead socket and STT
        transcripts never arrive. Symptom: user speaks, VAD fires
        (listening=True), but no turn lands in conversations.db and
        JARVIS stays silent forever.

        Detection: if voice was active recently but conversations.db
        hasn't been updated since before the voice ended, the STT
        pipeline is stuck. We restart both jarvis-voice-agent (drops
        the dead socket) and jarvis-voice-client (forces a fresh
        LiveKit room + job dispatch)."""
        # Wait past the first STALE_STT_SEC window before starting
        # checks so a fresh startup doesn't false-fire before the
        # first turn.
        await asyncio.sleep(STALE_STT_SEC + 30)
        db_path = Path.home() / ".jarvis" / "conversations.db"
        while not shutdown.is_set():
            try:
                self._check_stale_stt(db_path)
            except Exception as e:
                self.log.debug(f"[turn-watchdog] check error: {e}")
            try:
                await asyncio.wait_for(shutdown.wait(), timeout=30.0)
            except asyncio.TimeoutError:
                pass

    def _check_stale_stt(self, db_path: Path) -> None:
        """Fire once if the STT connection appears dead; schedule a
        restart."""
        if self._last_voice_active_ts == 0.0:
            return  # no voice activity this session yet
        now = time.time()
        voice_age = now - self._last_voice_active_ts
        # Only care about voice that ended between 90 s and
        # STALE_STT_SEC ago. <90 s: may still be processing (LLM +
        # TTS can take a moment). >STALE_STT_SEC: too old to blame on
        # a stale STT connection.
        if voice_age < 90 or voice_age > STALE_STT_SEC:
            return
        # Don't fire if the agent is actively doing something — those
        # update the DB at the end, so we'd false-positive mid-tool.
        if (
            self.state.listening or self.state.speaking
            or self.state.tool_running or self.state.agent_thinking
        ):
            return
        # Check whether conversations.db was updated after the voice
        # ended.
        try:
            db_mtime = db_path.stat().st_mtime
        except FileNotFoundError:
            return
        if db_mtime >= self._last_voice_active_ts:
            return  # DB was updated — turn landed, all good
        # Voice ended but no DB update — STT is dead.
        self.log.warning(
            f"[turn-watchdog] voice active {voice_age:.0f}s ago, "
            f"DB last updated {now - db_mtime:.0f}s ago — "
            "Groq STT connection appears dead, restarting agent"
        )
        # Clear the timestamp so the check doesn't re-fire while the
        # restart is in progress (takes ~5 s before this process exits).
        self._last_voice_active_ts = 0.0
        asyncio.create_task(self.restart_agent_unit(), name="stale-stt-restart")

    # ── OS-thread watchdog (loop wedge detection) ──────────────────

    def start_os_thread(self, loop: asyncio.AbstractEventLoop) -> None:
        """Capture the main loop reference + start the daemon
        watchdog thread. Call this once from `main()` after
        `loop = asyncio.get_running_loop()`."""
        self._main_loop = loop
        threading.Thread(
            target=self._watchdog_thread,
            name="loop-watchdog",
            daemon=True,
        ).start()

    def _watchdog_thread(self) -> None:
        """OS thread: kills the process if the asyncio loop stops
        updating the heartbeat. Daemon so it doesn't block normal
        exit."""
        # First update happens after the heartbeat task starts; give
        # it a generous grace window before we'd ever consider firing.
        grace_until = time.monotonic() + WATCHDOG_STALE_SEC + 30
        while True:
            time.sleep(WATCHDOG_POLL_SEC)
            if time.monotonic() < grace_until:
                continue
            with self._heartbeat_lock:
                age = time.monotonic() - self._last_heartbeat
            if age > WATCHDOG_STALE_SEC:
                self.log.error(
                    f"[watchdog] asyncio loop heartbeat stale ({age:.0f}s old) — "
                    f"killing process so systemd restarts us"
                )
                self._dump_stall_diagnostics(age)
                # os._exit (not sys.exit) — the loop is dead, atexit
                # handlers would deadlock waiting on it.
                os._exit(1)

    def _dump_stall_diagnostics(self, age: float) -> None:
        """Dump every Python thread stack and every pending asyncio
        task. Routed to log.error so the next stall NAMES its culprit
        instead of leaving us with `loop heartbeat stale (N s old)`
        and nothing else.

        Called from `_watchdog_thread` (an OS thread, not the asyncio
        loop) immediately before `os._exit(1)`. Best-effort: any
        failure here is swallowed so we never block the kill path that
        systemd relies on for clean restart."""
        self.log.error(
            "[watchdog-diag] === STALL %0.0fs OLD — DUMPING DIAGNOSTICS ===",
            age,
        )

        # 1. Every Python thread's current stack via faulthandler.
        try:
            self.log.error("[watchdog-diag] --- all-thread tracebacks ---")
            faulthandler.dump_traceback(file=sys.stderr, all_threads=True)
        except Exception as e:
            self.log.error("[watchdog-diag] faulthandler dump failed: %r", e)

        # 2. Every asyncio task on the captured main loop, with
        #    stack. Catches sync-over-async + long-running coroutines
        #    that never yield.
        try:
            if self._main_loop is None:
                self.log.error(
                    "[watchdog-diag] _main_loop unset — main() did not "
                    "capture the running loop. asyncio task dump skipped."
                )
            else:
                tasks = asyncio.all_tasks(loop=self._main_loop)
                self.log.error(
                    "[watchdog-diag] --- %d asyncio task(s) on main loop ---",
                    len(tasks),
                )
                for t in tasks:
                    try:
                        name = t.get_name() if hasattr(t, "get_name") else "?"
                        coro = getattr(t, "get_coro", lambda: None)()
                        coro_name = getattr(coro, "__qualname__", repr(coro))
                        self.log.error(
                            "[watchdog-diag] task %r coro=%s done=%s",
                            name, coro_name, t.done(),
                        )
                        frames = t.get_stack(limit=20)
                        if frames:
                            formatted = "".join(traceback.format_list(
                                traceback.extract_stack(frames[-1])
                            ))
                            self.log.error(
                                "[watchdog-diag] task %r stack:\n%s",
                                name, formatted,
                            )
                    except Exception as e:
                        self.log.error(
                            "[watchdog-diag] failed to dump task %r: %r",
                            t, e,
                        )
        except Exception as e:
            self.log.error("[watchdog-diag] asyncio task dump failed: %r", e)
