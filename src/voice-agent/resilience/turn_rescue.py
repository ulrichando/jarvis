"""Rescue user turns that complete against uninterruptible speech.

Echo-aware barge-in mode (JARVIS_ECHO_AWARE_BARGEIN=1, the default)
disables the framework's native interruption
(turn_handling.interruption.enabled=False) so JARVIS's own TTS echo
can't self-interrupt; a custom transcript-novelty layer force-interrupts
instead. The cost: every speech is formally allow_interruptions=False,
so when a user TURN completes while JARVIS is talking,
AgentActivity._user_turn_completed_task hits

    "skipping reply to user input, current speech generation cannot be
     interrupted"

and DISCARDS the turn entirely — not queued, not even added to chat_ctx.
Live 2026-07-02: 808 discards in one day, including directed commands
("I ask you to try if your web search is not online."). With
non-streaming local whisper the novelty layer only sees FINAL
transcripts, so its interrupt races turn-completion and loses regularly.
Upstream has no rescue/queue option — livekit/agents#1613/#3230/#4443
document this drop/wedge class.

The patch wraps `_user_turn_completed_task`: when the current speech is
uninterruptible AND the completed transcript is NOT JARVIS's own echo
(the same `pipeline.echo_gate.is_echo` check the barge-in layer uses),
flip the speech handle's `_allow_interruptions` to True before
delegating — the framework's own path then interrupts it cleanly and
generates the reply. Echo transcripts keep the old behavior (dropped),
which is exactly why interruptions were disabled in the first place.

Idempotent install(). Kill-switch: JARVIS_TURN_RESCUE_DISABLED=1.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger("jarvis.turn_rescue")

_INSTALLED = False


def enabled() -> bool:
    return os.environ.get("JARVIS_TURN_RESCUE_DISABLED", "0") != "1"


def should_rescue(transcript: str, speech_allows_interruptions: bool) -> bool:
    """Pure decision: rescue this completed turn?

    True only when the active speech is uninterruptible AND the
    transcript carries novel (non-echo) content. Any failure in the
    echo check fails SAFE (no rescue → old drop behavior), because
    rescuing an echo would let JARVIS interrupt itself with its own
    voice — the exact failure echo-aware mode exists to prevent.
    """
    if not enabled():
        return False
    if speech_allows_interruptions:
        return False  # framework handles it normally
    text = (transcript or "").strip()
    if not text:
        return False
    try:
        from pipeline import echo_gate, speaking_tracker
        return not echo_gate.is_echo(
            text, speaking_tracker.current_speaking_text()
        )
    except Exception as e:
        logger.debug(f"[turn-rescue] echo check failed ({e}); not rescuing")
        return False


def install() -> None:
    """Monkey-patch AgentActivity._user_turn_completed_task. Idempotent."""
    global _INSTALLED
    if _INSTALLED:
        return

    try:
        from livekit.agents.voice import agent_activity as aa
    except ImportError:
        logger.warning("[turn-rescue] agent_activity unavailable; skipped")
        _INSTALLED = True
        return

    if getattr(aa.AgentActivity, "_jarvis_turn_rescue_patched", False):
        _INSTALLED = True
        return

    orig = aa.AgentActivity._user_turn_completed_task

    async def patched(self, old_task, info):
        try:
            cs = getattr(self, "_current_speech", None)
            transcript = (getattr(info, "new_transcript", "") or "")
            if cs is not None and should_rescue(
                transcript, bool(getattr(cs, "allow_interruptions", True))
            ):
                # Flip the handle interruptible; the original method then
                # takes its own clean interrupt-and-reply path instead of
                # the discard branch.
                cs._allow_interruptions = True
                logger.info(
                    "[turn-rescue] novel turn vs uninterruptible speech — "
                    f"made speech interruptible: {transcript[:60]!r}"
                )
                try:
                    sess = getattr(self, "_session", None)
                    if sess is not None:
                        sess._jarvis_was_interrupted = True
                except Exception:
                    pass
        except Exception as e:
            logger.debug(f"[turn-rescue] pre-check failed (non-fatal): {e}")
        return await orig(self, old_task, info)

    aa.AgentActivity._user_turn_completed_task = patched
    aa.AgentActivity._jarvis_turn_rescue_patched = True
    _INSTALLED = True
    logger.info("[turn-rescue] installed (novel turns rescue uninterruptible speech)")
