"""Process-local record of the text JARVIS is currently / was just speaking.

Fed by the Orpheus TTS shim (`providers/tts.py`), which has no AgentSession
reference — so it can't write `session._jarvis_speaking_text` as the design
first assumed. One LiveKit worker job handles one session, so process-local
state is session-scoped in practice; `reset()` is called per speech-start path
to avoid cross-job bleed.

Consumed by the echo-aware barge-in gate (`pipeline/echo_gate.py`):
  - current_speaking_text()    — what JARVIS is saying NOW   (interrupt consumer)
  - recent_speaking_text(ttl)  — what JARVIS just said, within `ttl` seconds of
                                 speech end (phantom-turn consumer — a finalized
                                 echo turn arrives AFTER speech ends, by which
                                 point the live buffer is already cleared)

A lock guards the state: TTS synthesis and the agent's event handlers normally
share one event loop, but the lock is cheap insurance against any executor
thread and keeps the reads/writes atomic.

Spec: docs/superpowers/specs/2026-05-20-echo-aware-bargein-gate-design.md
"""
from __future__ import annotations

import threading
import time

__all__ = [
    "note_speaking",
    "mark_speech_ended",
    "current_speaking_text",
    "recent_speaking_text",
    "reset",
]

_lock = threading.Lock()
_current_parts: list[str] = []
_recent_text: str = ""
_recent_ended_at: float = 0.0


def note_speaking(text: str) -> None:
    """Append a synthesized chunk to the live buffer (called from TTS `_run`)."""
    if not text:
        return
    with _lock:
        _current_parts.append(text)


def mark_speech_ended() -> None:
    """Snapshot the live buffer into the recent buffer (stamped) and clear the
    live buffer. Called when `agent_state` leaves "speaking"."""
    global _recent_text, _recent_ended_at
    with _lock:
        if _current_parts:
            _recent_text = " ".join(_current_parts)
            _recent_ended_at = time.monotonic()
            _current_parts.clear()


def current_speaking_text() -> str:
    """Text JARVIS is speaking right now ('' if not speaking)."""
    with _lock:
        return " ".join(_current_parts)


def recent_speaking_text(ttl_s: float = 2.0) -> str:
    """Text JARVIS is speaking now, or — if speech just ended within `ttl_s`
    seconds — what it just finished saying. '' once the snapshot is stale."""
    with _lock:
        if _current_parts:
            return " ".join(_current_parts)
        if _recent_text and (time.monotonic() - _recent_ended_at) <= ttl_s:
            return _recent_text
        return ""


def reset() -> None:
    """Clear all state (per-session/per-job boundary; also used by tests)."""
    global _recent_text, _recent_ended_at
    with _lock:
        _current_parts.clear()
        _recent_text = ""
        _recent_ended_at = 0.0
