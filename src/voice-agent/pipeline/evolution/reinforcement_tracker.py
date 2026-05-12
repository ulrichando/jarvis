"""Producer D — per-turn reinforcement tracker.

When a rule's keywords appear in the user turn AND no correction
follows within the configured window, the rule's reinforcement
count is incremented. Used by lifecycle.promote() to decide
accepted → core eligibility (≥10 reinforcing turns + 30 days).

Trigger keyword extraction is intentionally crude — a regex over
the rule text's quoted strings + verbs. For the v1 cut, the keyword
set is the rule's first ≥4-char word tokens. This is good enough
for the Chrome / Yes? / silent-hours rules; future iterations can
swap in an LLM-derived keyword extraction.
"""
from __future__ import annotations

import logging
import re
from collections import Counter
from typing import Iterable

from .schema import Rule


__all__ = ["ReinforcementTracker"]


logger = logging.getLogger("jarvis.evolution.reinforcement")


_TOKEN_RE = re.compile(r"\b[a-zA-Z][a-zA-Z0-9_-]{3,}\b")


def _trigger_tokens(text: str) -> set[str]:
    quoted = re.findall(r'"([^"]+)"', text)
    pool = " ".join(quoted) if quoted else text
    return {tok.lower() for tok in _TOKEN_RE.findall(pool)[:4]}


class ReinforcementTracker:
    def __init__(self, rules: Iterable[Rule]) -> None:
        self._rules = list(rules)
        self._tokens = {r.id: _trigger_tokens(r.text) for r in self._rules}
        self._counts: Counter[str] = Counter()

    def _applies(self, rule_id: str, user_text: str) -> bool:
        toks = self._tokens.get(rule_id) or set()
        if not toks:
            return False
        low = (user_text or "").lower()
        return any(t in low for t in toks)

    def observe(
        self,
        *,
        turn_id: str,
        user_text: str,
        jarvis_text: str,
        next_user_correction: bool,
    ) -> None:
        if next_user_correction:
            return
        for r in self._rules:
            if r.tier not in ("staged", "accepted"):
                continue
            if self._applies(r.id, user_text):
                self._counts[r.id] += 1

    def reinforcement_count(self, rule_id: str) -> int:
        return int(self._counts.get(rule_id, 0))

    def snapshot(self) -> dict[str, int]:
        return dict(self._counts)
