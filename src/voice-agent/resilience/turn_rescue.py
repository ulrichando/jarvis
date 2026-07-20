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
Live 2026-06/07: 808 discards across 6/25-7/02 (peak 420/day), including
directed commands ("I ask you to try if your web search is not online.").
With non-streaming local whisper the novelty layer only sees FINAL
transcripts, so its interrupt races turn-completion and loses regularly.
Upstream has no rescue/queue option — livekit/agents#1613/#3230/#4443
document this drop/wedge class (verified 2026-07-04: two closed
not-planned, one open; main @1.6.4 has the identical order).

The patch wraps `_user_turn_completed_task`: when the current speech is
uninterruptible AND the completed transcript is NOT JARVIS's own echo
(the same `pipeline.echo_gate.is_echo` check the barge-in layer uses),
flip the speech handle's `_allow_interruptions` to True before
delegating — the framework's own path then interrupts it cleanly and
generates the reply. Echo transcripts keep the old behavior (dropped),
which is exactly why interruptions were disabled in the first place.

Two hardenings (2026-07-04, after the rescue inverted the original bug):
the framework interrupts the current speech BEFORE on_user_turn_completed
runs the discard gates (agent_activity.py: interrupt ~20 lines ahead of
the StopResponse sites), so a doomed transcript that rescues kills an
in-flight delivery and then dies in the gates — completed tool work,
never voiced (live traces: TV babble 'Mommy. Mommy.' / silent-mode
suppressed 'see you all time' each killed a finished web_search reply).

1. GATE-VETO — `should_rescue` consults the agent's
   `_jarvis_would_discard_transcript` (a pure mirror of the silent-mode /
   stt-garbage / unaddressed-ambient gates): a transcript destined for
   StopResponse never flips the speech.
2. RESURRECTION — if a rescue DID kill the speech and the rescuing turn
   then produces no reply of its own within a short grace (gated away, or
   the LLM stayed silent per DISCRETION), regenerate the interrupted
   delivery from chat_ctx (the tool results are still there). Skipped for
   deliberate stops / silent mode via `_jarvis_resurrect_blocked`, and
   cooldown-limited so ambient storms can't ping-pong replies.

Idempotent install(). Kill-switches: JARVIS_TURN_RESCUE_DISABLED=1 (whole
patch), JARVIS_TURN_RESCUE_RESURRECT_DISABLED=1 (resurrection only),
JARVIS_INTERRUPT_FOLLOWUP_DISABLED=1 (the circle-back-to-the-interrupted-
question path only).
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Callable, Optional

logger = logging.getLogger("jarvis.turn_rescue")

_INSTALLED = False

# Resurrection tuning. The grace must outlive the gated turn's StopResponse
# (instant) and a DISCRETION-empty LLM round (sub-second on the fast rung)
# but stay short enough that the recovered reply still feels attached to
# the user's request. The cooldown bounds ambient-storm ping-pong: at most
# one resurrected delivery per window, later kills stay dead until it laps.
_RESURRECT_GRACE_S = 1.6
_RESURRECT_COOLDOWN_S = 10.0
_last_resurrect_mono = 0.0
# Monotonic id of the most recent rescue. A pending resurrection check
# aborts when a newer rescue happened during its grace sleep — the newer
# event's own check owns the outcome (regeneration reads chat_ctx, so one
# resurrection recovers the whole cascade).
_rescue_seq = 0

# Interrupt-follow-up tuning (2026-07-19). Sibling to resurrection but for the
# OPPOSITE case: the user barged in with question B, JARVIS answered B, and the
# question A he was interrupted on was silently dropped. This circles back to A
# once, after B's reply finishes. Waits for agent_state to RETURN to listening
# (B's reply drained), up to _FOLLOWUP_MAX_WAIT_S; cooldown-bounded so it can
# never loop.
_FOLLOWUP_GRACE_S = 0.8
_FOLLOWUP_MAX_WAIT_S = 20.0
_FOLLOWUP_COOLDOWN_S = 20.0
_last_followup_mono = 0.0


def enabled() -> bool:
    return os.environ.get("JARVIS_TURN_RESCUE_DISABLED", "0") != "1"


def resurrect_enabled() -> bool:
    return os.environ.get("JARVIS_TURN_RESCUE_RESURRECT_DISABLED", "0") != "1"


def followup_enabled() -> bool:
    return os.environ.get("JARVIS_INTERRUPT_FOLLOWUP_DISABLED", "0") != "1"


def should_rescue(
    transcript: str,
    speech_allows_interruptions: bool,
    discard_probe: Optional[Callable[[str], bool]] = None,
) -> bool:
    """Pure decision: rescue this completed turn?

    True only when the active speech is uninterruptible AND the
    transcript carries novel (non-echo) content AND the turn gates would
    not discard it anyway. Any failure in the echo check fails SAFE (no
    rescue → old drop behavior), because rescuing an echo would let
    JARVIS interrupt itself with its own voice — the exact failure
    echo-aware mode exists to prevent.

    `discard_probe` is the agent's `_jarvis_would_discard_transcript`.
    Only a literal True vetoes (`is True`, not truthiness) so a Mock or
    misbehaving probe can never change rescue behavior; a raising probe
    is ignored the same way.
    """
    if not enabled():
        return False
    if speech_allows_interruptions:
        return False  # framework handles it normally
    text = (transcript or "").strip()
    if not text:
        return False
    if discard_probe is not None:
        try:
            if discard_probe(text) is True:
                logger.info(
                    "[turn-rescue] transcript would be discarded by the turn "
                    f"gates — not rescuing with it: {text[:60]!r}"
                )
                return False
        except Exception as e:
            logger.debug(f"[turn-rescue] discard probe failed ({e}); ignoring")
    try:
        from pipeline import echo_gate, speaking_tracker
        return not echo_gate.is_echo(
            text, speaking_tracker.current_speaking_text()
        )
    except Exception as e:
        logger.debug(f"[turn-rescue] echo check failed ({e}); not rescuing")
        return False


def _schedule_resurrect(activity: Any, killed_speech: Any, transcript: str, seq: int) -> None:
    """Arm a delayed check: if the turn that rescued (killed) `killed_speech`
    produces NO speech of its own within the grace window — StopResponse'd
    by a gate the probe doesn't mirror, or the LLM answered ambient audio
    with an empty string — regenerate so the interrupted delivery isn't
    lost. Best-effort: any missing surface silently skips."""
    if not resurrect_enabled():
        return
    sess = getattr(activity, "_session", None)
    if sess is None:
        return
    blocked = getattr(getattr(activity, "_agent", None), "_jarvis_resurrect_blocked", None)
    if blocked is not None:
        try:
            if blocked(transcript) is True:
                return  # deliberate stop / silent mode — stay dead
        except Exception:
            return

    async def _resurrect_after_grace() -> None:
        global _last_resurrect_mono
        try:
            await asyncio.sleep(_RESURRECT_GRACE_S)
            if _rescue_seq != seq:
                return  # superseded — the newer rescue's check owns the outcome
            if getattr(killed_speech, "interrupted", False) is not True:
                return  # delivery actually completed; nothing was lost
            if getattr(sess, "agent_state", "") not in ("listening", "idle"):
                return  # the rescuing turn IS producing a reply of its own
            now = time.monotonic()
            if (now - _last_resurrect_mono) < _RESURRECT_COOLDOWN_S:
                return
            # Symmetric latch (Fable review 2026-07-19): if the interrupt
            # follow-up just circled back to the question, don't ALSO resurrect —
            # both would re-answer it. Guards the short-reply race should
            # _FOLLOWUP_GRACE_S ever be tuned below the resurrection grace.
            if (now - _last_followup_mono) < _RESURRECT_COOLDOWN_S:
                return
            _last_resurrect_mono = now
            logger.info(
                "[turn-rescue] rescuing turn produced no reply — "
                "resurrecting the interrupted delivery"
            )
            sess.generate_reply(
                instructions=(
                    "You were interrupted a moment ago while responding. "
                    "Deliver that response now — if you had just finished "
                    "tool work, give the user its result. Do not mention "
                    "the interruption."
                )
            )
        except Exception as e:
            logger.debug(f"[turn-rescue] resurrect skipped: {e}")

    try:
        asyncio.create_task(_resurrect_after_grace())
    except RuntimeError:
        pass  # no running loop (sync/unit-test context) — best-effort only


def _schedule_question_followup(activity: Any, transcript: str, seq: int) -> None:
    """After a barge-in whose OWN reply finished, circle back ONCE to the prior
    QUESTION that barge-in interrupted — the case resurrection deliberately
    skips (resurrection only fires when the barge-in produced NO reply). This
    is the "you asked A, got answered on B, and A was dropped" fix. Guarded
    against deliberate stops, topic changes, and double-firing with
    resurrection."""
    if not followup_enabled():
        return
    sess = getattr(activity, "_session", None)
    if sess is None:
        return
    prior_q = getattr(sess, "_jarvis_prior_pending_question", None)
    if not prior_q:
        return  # the interrupted turn wasn't a question — nothing owed
    blocked = getattr(getattr(activity, "_agent", None), "_jarvis_resurrect_blocked", None)
    if blocked is not None:
        try:
            # Screen BOTH the barge-in transcript (a deliberate stop) AND the
            # prior "question" itself — "can you stop" / "will you shut up" are
            # question-shaped but must never be circled back to. (Fable review.)
            if blocked(transcript) is True or blocked(prior_q) is True:
                return
        except Exception:
            return

    async def _followup_after_reply() -> None:
        global _last_followup_mono
        try:
            waited = 0.0
            saw_speaking = False
            # Wait for the barge-in's OWN reply to run and drain (speaking →
            # listening). If it never speaks, the barge-in produced no reply —
            # that's resurrection's job, not ours — so we drop. This
            # speaking→listening transition is what makes follow-up and
            # resurrection mutually exclusive.
            while waited < _FOLLOWUP_MAX_WAIT_S:
                await asyncio.sleep(_FOLLOWUP_GRACE_S)
                waited += _FOLLOWUP_GRACE_S
                if _rescue_seq != seq:
                    return  # a newer rescue owns the conversation now
                st = getattr(sess, "agent_state", "")
                if st == "speaking":
                    saw_speaking = True
                elif saw_speaking and st in ("listening", "idle"):
                    break
            else:
                return  # the reply never ran + drained within the budget
            now = time.monotonic()
            if (now - _last_followup_mono) < _FOLLOWUP_COOLDOWN_S:
                return
            # If resurrection JUST re-delivered the interrupted answer (short
            # barge-in reply that drained before resurrection's grace), don't
            # also circle back — both would answer the same question.
            if (now - _last_resurrect_mono) < _FOLLOWUP_COOLDOWN_S:
                return
            # The stash must STILL be the same question — a newer question
            # replaced it (topic change) → don't chase a stale one.
            if getattr(sess, "_jarvis_prior_pending_question", None) != prior_q:
                return
            _last_followup_mono = now
            sess._jarvis_prior_pending_question = None  # consume — fire once
            logger.info(
                "[turn-rescue] circling back to the interrupted question: "
                f"{prior_q[:60]!r}"
            )
            sess.generate_reply(
                instructions=(
                    f'A moment ago the user asked: "{prior_q}". You have handled '
                    "what they interrupted with. Circle back and answer their "
                    'earlier question now, briefly — e.g. "Coming back to your '
                    'earlier question —…". If you already answered it in passing '
                    "or it no longer makes sense, stay silent."
                )
            )
        except Exception as e:
            logger.debug(f"[turn-rescue] followup skipped: {e}")

    try:
        asyncio.create_task(_followup_after_reply())
    except RuntimeError:
        pass  # no running loop (sync/unit-test) — best-effort only


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
        global _rescue_seq
        rescued_seq = None
        killed_speech = None
        transcript = ""
        try:
            cs = getattr(self, "_current_speech", None)
            transcript = (getattr(info, "new_transcript", "") or "")
            probe = getattr(
                getattr(self, "_agent", None), "_jarvis_would_discard_transcript", None
            )
            if cs is not None and should_rescue(
                transcript,
                bool(getattr(cs, "allow_interruptions", True)),
                discard_probe=probe,
            ):
                # Flip the handle interruptible; the original method then
                # takes its own clean interrupt-and-reply path instead of
                # the discard branch.
                cs._allow_interruptions = True
                _rescue_seq += 1
                rescued_seq = _rescue_seq
                killed_speech = cs
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
        result = await orig(self, old_task, info)
        if rescued_seq is not None:
            try:
                _schedule_resurrect(self, killed_speech, transcript, rescued_seq)
            except Exception as e:
                logger.debug(f"[turn-rescue] resurrect scheduling failed: {e}")
            try:
                _schedule_question_followup(self, transcript, rescued_seq)
            except Exception as e:
                logger.debug(f"[turn-rescue] followup scheduling failed: {e}")
        return result

    aa.AgentActivity._user_turn_completed_task = patched
    aa.AgentActivity._jarvis_turn_rescue_patched = True
    _INSTALLED = True
    logger.info("[turn-rescue] installed (novel turns rescue uninterruptible speech)")
