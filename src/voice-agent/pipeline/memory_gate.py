# src/voice-agent/pipeline/memory_gate.py
"""Engagement gate for memory WRITES.

Root cause (2026-05-20 spike): the hot mic transcribes ambient TV/media
audio into coherent role='user' turns; the extractor + capture-trigger
fire on them, polluting state.db.memories (~22% of the store was ambient
hallucinations). The only prior defense (quiet-hours gate) was time-boxed
and defeated by _recent_interaction(), so daytime ambient sailed through.

This gate arms a rolling window on a 'Jarvis' vocative and only allows
memory writes inside it. Ambient TV almost never says 'Jarvis', so cold
ambient turns are refused. Anchored on the vocative, NOT an assistant
reply — the spike found JARVIS sometimes replied to ambient TV, so a reply
anchor would re-open the window for ambient audio.

Gates WRITES only (extractor + capture-trigger); does NOT change what
JARVIS replies to. Kill-switch: JARVIS_MEMORY_ENGAGEMENT_GATE=0.
"""
from __future__ import annotations

import os
import time

_LAST_VOCATIVE_AT: float | None = None
_DEFAULT_WINDOW_S = 180.0


def _gate_enabled() -> bool:
    return os.environ.get("JARVIS_MEMORY_ENGAGEMENT_GATE", "1") != "0"


def _window_s() -> float:
    try:
        return float(os.environ.get("JARVIS_MEMORY_ENGAGEMENT_WINDOW_S",
                                    str(_DEFAULT_WINDOW_S)))
    except (TypeError, ValueError):
        return _DEFAULT_WINDOW_S


def note_vocative(now: float | None = None) -> None:
    """Arm the engagement window. Call when a turn contains a 'Jarvis'
    vocative (or a deliberate wake phrase)."""
    global _LAST_VOCATIVE_AT
    _LAST_VOCATIVE_AT = time.monotonic() if now is None else now


def is_write_engaged(now: float | None = None,
                     window_s: float | None = None) -> bool:
    """True if a memory WRITE should be allowed this turn. Gate disabled
    (env=0) => always True (legacy). Else True iff a vocative armed the
    window within window_s."""
    if not _gate_enabled():
        return True
    if _LAST_VOCATIVE_AT is None:
        return False
    now = time.monotonic() if now is None else now
    window_s = _window_s() if window_s is None else window_s
    return (now - _LAST_VOCATIVE_AT) <= window_s


def reset() -> None:
    """Test seam — clear the armed window."""
    global _LAST_VOCATIVE_AT
    _LAST_VOCATIVE_AT = None
