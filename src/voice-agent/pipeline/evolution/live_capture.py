"""Producer A — per-turn correction-phrase observer.

Runs on the post-turn hook (after the assistant turn is committed,
NOT during the user-facing path). When the user's latest turn
contains a correction phrase, emits a structured proposal carrying:
  - the immediately-prior JARVIS turn as evidence
  - the correction text as evidence_quote
  - a pattern label derived from the matched phrase

The observer is stateful only within a single session — recent
correction texts are kept in a small ring to dedup consecutive
restatements of the same complaint.
"""
from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass
from typing import Optional

from . import audit_log


__all__ = ["LiveCapture", "_CORRECTION_PHRASES"]


logger = logging.getLogger("jarvis.evolution.live_capture")


_CORRECTION_PHRASES = [
    "that was wrong",
    "you keep doing",
    "don't do that",
    "never do that",
    "stop doing",
    "why did you",
    "that's not what",
    "didn't ask you to",
    "i didn't say",
    "you got it wrong",
    "that's incorrect",
    "you're wrong",
    "don't open",
    "don't play",
    "don't start",
    "i never asked",
    "no, i meant",
    "not chromium",
    "wrong app",
]


@dataclass
class _Recent:
    turn_id: str
    user_text: str
    jarvis_text: str


class LiveCapture:
    def __init__(self, *, dedup_window: int = 5) -> None:
        self._prior: Optional[_Recent] = None
        self._recent_corrections: deque[str] = deque(maxlen=dedup_window)

    @staticmethod
    def _matched_phrase(text: str) -> Optional[str]:
        low = text.lower()
        for phrase in _CORRECTION_PHRASES:
            if phrase in low:
                return phrase
        return None

    def observe(
        self, *, turn_id: str, user_text: str, jarvis_text: str
    ) -> Optional[dict]:
        phrase = self._matched_phrase(user_text or "")
        prior = self._prior
        self._prior = _Recent(
            turn_id=turn_id,
            user_text=user_text or "",
            jarvis_text=jarvis_text or "",
        )
        if phrase is None or prior is None:
            return None

        normalized = (user_text or "").strip().lower()
        if normalized in self._recent_corrections:
            return None
        self._recent_corrections.append(normalized)

        prior_summary = (prior.jarvis_text or "").strip()[:120]
        if prior_summary:
            synthesized_rule = (
                f"When the user expresses a correction ('{phrase}'), "
                f"avoid repeating the pattern that led to: \"{prior_summary}\""
            )
        else:
            synthesized_rule = (
                f"When the user expresses a correction with phrase '{phrase}', "
                f"adjust behavior accordingly."
            )

        proposal = {
            "source": "live_capture",
            "matched_phrase": phrase,
            "pattern": f"User correction triggered by '{phrase}'",
            "rule": synthesized_rule,
            "evidence_quote": user_text,
            "evidence_turns": [prior.turn_id, turn_id],
            "prior_jarvis": prior.jarvis_text,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        audit_log.append_event(
            kind="live_capture_proposal",
            matched_phrase=phrase,
            evidence_turns=proposal["evidence_turns"],
        )
        logger.info(
            f"[live-capture] matched '{phrase}' at {turn_id} → proposal queued"
        )
        return proposal
