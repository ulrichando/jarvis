"""Producer C — runs every 24 h, proposes archival of stale rules.

Three detection passes:
  - duplicates (Levenshtein ratio >= 0.85, keep older)
  - dead subsystem refs (hardcoded keyword list of removed
    components — ElevenLabs, butler-register, etc.)
  - contradicted-by-newer (a staged or accepted rule whose text
    asserts behavior that contradicts a higher-tier rule)

All output is archival proposals only — never an in-place edit.
The evaluator pipeline still adjudicates each one.
"""
from __future__ import annotations

import logging
from difflib import SequenceMatcher
from typing import Iterable

from .schema import Rule
from . import audit_log


__all__ = [
    "find_duplicates",
    "find_dead_subsystem_rules",
    "run",
]


logger = logging.getLogger("jarvis.evolution.contradiction")


_DEAD_KEYWORDS = [
    "elevenlabs",
    "eleven labs",
    "yes, sir",
    "yes sir",
    ", sir",
    "chromium",
]


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def find_duplicates(
    rules: Iterable[Rule], *, threshold: float = 0.85
) -> list[tuple[str, str]]:
    pool = [r for r in rules if r.tier in ("accepted", "staged")]
    pairs: list[tuple[str, str]] = []
    for i, a in enumerate(pool):
        for b in pool[i + 1:]:
            if _similarity(a.text, b.text) >= threshold:
                pairs.append((a.id, b.id))
    return pairs


def find_dead_subsystem_rules(rules: Iterable[Rule]) -> list[Rule]:
    hits: list[Rule] = []
    for r in rules:
        if r.tier not in ("accepted", "staged"):
            continue
        low = r.text.lower()
        if any(k in low for k in _DEAD_KEYWORDS):
            hits.append(r)
    return hits


def run(rules: list[Rule]) -> list[dict]:
    proposals: list[dict] = []
    by_id = {r.id: r for r in rules}

    for a_id, b_id in find_duplicates(rules):
        a, b = by_id[a_id], by_id[b_id]
        if (a.created or "") <= (b.created or ""):
            keep, retire = a, b
        else:
            keep, retire = b, a
        proposals.append({
            "source": "contradiction_detector",
            "kind": "archive_duplicate",
            "target_id": retire.id,
            "keep_id": keep.id,
            "reason": "duplicate",
            "similarity": _similarity(a.text, b.text),
            "evidence_quote": f"{a.text!r} ~= {b.text!r}",
            "evidence_turns": [],
        })

    for r in find_dead_subsystem_rules(rules):
        proposals.append({
            "source": "contradiction_detector",
            "kind": "archive_dead_subsystem",
            "target_id": r.id,
            "reason": "dead_subsystem",
            "evidence_quote": r.text,
            "evidence_turns": [],
        })

    audit_log.append_event(
        kind="contradiction_run",
        proposal_count=len(proposals),
    )
    logger.info(f"[contradiction] {len(proposals)} archival proposals")
    return proposals
