"""Detect LiveKit jobs that initialize successfully but never receive
any audio — the zombie-subscription failure mode observed live on
2026-05-17/18.

Symptom: after a voice-client reconnect, the agent's worker spawns a
brand-new Job, completes initialization (joins room, resolves LLM +
TTS dispatchers, logs `[dispatch] LLM dispatcher resolved`), and then
sits there with NO STT events firing — because the agent's track
subscription bound to a stale publisher track ID. Self-heal took
2.5 hours via three independent worker crashes.

This module gives the agent a 90 s deadman switch: if a job has been
initialized but no audio activity has been observed within
`SILENCE_TIMEOUT_S`, force the process to exit. The systemd unit will
restart it, dropping the zombie state.

Public surface:
  - `mark_job_started()` — called when a new Job is dispatched. Resets
    the watchdog clock.
  - `mark_audio_activity()` — called when ANY audio signal fires (STT
    transcript, voice-publisher heartbeat, user-turn-completed event).
    Bumps the activity timestamp.
  - `start_audio_silence_watchdog_task()` — schedules the background
    poller on the current asyncio loop. Idempotent.

Env:
  - `JARVIS_AUDIO_SILENCE_TIMEOUT_S` — silence budget before exit
    (default 90). Set to 0 to disable the watchdog.
  - `JARVIS_AUDIO_SILENCE_CHECK_INTERVAL_S` — poll cadence (default 10).

Spec: docs/superpowers/specs/2026-05-04-jarvis-voice-resilience-design.md
(audio-silence detector added 2026-05-18 after live incident).
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from typing import Optional


__all__ = [
    "mark_job_started",
    "mark_audio_activity",
    "start_audio_silence_watchdog_task",
    "is_running",
]


logger = logging.getLogger("jarvis.audio_silence_watchdog")


_SILENCE_TIMEOUT_S = float(os.environ.get("JARVIS_AUDIO_SILENCE_TIMEOUT_S", "90"))
_CHECK_INTERVAL_S = float(os.environ.get("JARVIS_AUDIO_SILENCE_CHECK_INTERVAL_S", "10"))


# Module-level state. monotonic seconds; 0 means "no event yet".
_last_job_started_ts: float = 0.0
_last_audio_activity_ts: float = 0.0
_task: Optional[asyncio.Task] = None
_exit_called: bool = False


def mark_job_started() -> None:
    """Reset the deadman clock. Call when a fresh LiveKit Job is
    dispatched (the agent has just initialized, has NOT yet processed
    audio)."""
    global _last_job_started_ts, _last_audio_activity_ts
    now = time.monotonic()
    _last_job_started_ts = now
    # Reset audio-activity too so the budget starts fresh — otherwise
    # an old activity timestamp from a previous job would mask the
    # zombie state.
    _last_audio_activity_ts = now
    logger.debug("[audio-silence] job started; clock reset")


def mark_audio_activity() -> None:
    """Bump the activity timestamp. Call on ANY user-audio signal
    (STT interim/final transcript, voice-publisher tick, user-turn-
    completed event). Cheap; can be called from a hot path."""
    global _last_audio_activity_ts
    _last_audio_activity_ts = time.monotonic()


def is_running() -> bool:
    """True if the background task has been started + hasn't exited."""
    return _task is not None and not _task.done()


async def _watchdog_loop(stop: Optional[asyncio.Event]) -> None:
    """Periodic check loop. Compares silence since the most-recent
    activity (or job start) against the budget."""
    logger.info(
        f"[audio-silence] watchdog active "
        f"(timeout={_SILENCE_TIMEOUT_S:.0f}s, "
        f"check={_CHECK_INTERVAL_S:.0f}s)"
    )
    try:
        while True:
            try:
                if stop is not None:
                    await asyncio.wait_for(stop.wait(), timeout=_CHECK_INTERVAL_S)
                    return  # stop event fired
                else:
                    await asyncio.sleep(_CHECK_INTERVAL_S)
            except asyncio.TimeoutError:
                pass

            if _last_job_started_ts == 0.0:
                # No job ever started — nothing to compare against.
                # Common case at fresh agent startup before any user
                # session arrives.
                continue
            now = time.monotonic()
            silence = now - max(_last_job_started_ts, _last_audio_activity_ts)
            if silence < _SILENCE_TIMEOUT_S:
                continue

            # Budget exceeded. The job is alive but receiving no audio.
            global _exit_called
            if _exit_called:
                # Already fired; the loop just hasn't been cancelled yet.
                continue
            _exit_called = True
            logger.error(
                f"[audio-silence] no audio for {silence:.1f}s after job "
                f"start (budget={_SILENCE_TIMEOUT_S:.0f}s); forcing exit. "
                f"systemd will restart the process with a clean LiveKit "
                f"subscription state."
            )
            # sys.exit(1) raises SystemExit; the running task gets cancelled
            # cleanly. systemd unit's Restart=on-failure brings us back.
            sys.exit(1)
    except asyncio.CancelledError:
        logger.debug("[audio-silence] watchdog cancelled")
        raise
    except SystemExit:
        raise
    except Exception:
        logger.exception("[audio-silence] watchdog crashed")


def start_audio_silence_watchdog_task(
    stop: Optional[asyncio.Event] = None,
) -> Optional[asyncio.Task]:
    """Schedule the background watchdog on the current loop. Idempotent
    — calling twice returns the existing task without spawning a second.

    Returns None if disabled (timeout=0) or already running.
    """
    global _task
    if _SILENCE_TIMEOUT_S <= 0:
        logger.info("[audio-silence] disabled via JARVIS_AUDIO_SILENCE_TIMEOUT_S=0")
        return None
    if _task is not None and not _task.done():
        return _task
    _task = asyncio.create_task(
        _watchdog_loop(stop), name="audio-silence-watchdog"
    )
    return _task
