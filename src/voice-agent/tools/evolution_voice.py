"""Voice tools for the evolution loop.

`*_impl` functions are the testable coroutine bodies. The decorated
`@function_tool` wrappers below them are what gets registered with
the supervisor's tool surface.
"""
from __future__ import annotations

import logging
from difflib import SequenceMatcher
from pathlib import Path

from livekit.agents.llm import function_tool

from pipeline.evolution import audit_log, lifecycle, report
from pipeline.evolution.store import (
    AnchorWriteRefused,
    RuleStore,
)


__all__ = [
    "evolution_status_impl",
    "evolution_report_impl",
    "revert_rule_impl",
    "review_staged_rules_impl",
    "promote_rule_impl",
    "evolution_status",
    "evolution_report",
    "revert_rule",
    "review_staged_rules",
    "promote_rule",
]


logger = logging.getLogger("jarvis.evolution.voice_tools")


def _default_store() -> RuleStore:
    return RuleStore()


async def evolution_status_impl() -> str:
    store = _default_store()
    loaded = store.load()
    return (
        f"{len(loaded.anchor)} anchor, {len(loaded.core)} in core, "
        f"{len(loaded.accepted)} accepted, {len(loaded.staged)} staged, "
        f"{len(loaded.archived)} archived."
    )


async def evolution_report_impl(when: str = "today") -> str:
    if not report.REPORT_PATH.exists():
        return "No evolution report yet — first run hasn't fired."
    text = report.REPORT_PATH.read_text(encoding="utf-8")
    return text[:1800]


async def revert_rule_impl(query: str) -> str:
    store = _default_store()
    loaded = store.load()
    for r in loaded.anchor:
        if SequenceMatcher(None, r.text.lower(), query.lower()).ratio() > 0.5:
            return (
                f"Cannot revert anchor rule {r.id} from runtime — "
                "anchor edits go through commit + review."
            )

    candidates = [
        (SequenceMatcher(None, r.text.lower(), query.lower()).ratio(), r)
        for r in (loaded.core + loaded.accepted + loaded.staged)
    ]
    if not candidates:
        return "No matching rule found."
    best_score, best = max(candidates, key=lambda x: x[0])
    if best_score < 0.4:
        return f"No close match for query {query!r}."
    try:
        lifecycle.rollback(
            store, rule_id=best.id, reason=f"user voice revert: {query}",
            retirement_reason="user_revert",
        )
    except AnchorWriteRefused:
        return f"Refused — {best.id} is an anchor."
    return f"Reverted {best.id}: {best.text[:120]!r}"


async def review_staged_rules_impl() -> str:
    store = _default_store()
    loaded = store.load()
    if not loaded.staged:
        return "No staged rules."
    lines = [f"{len(loaded.staged)} staged rule(s):"]
    for r in loaded.staged:
        lines.append(f"  {r.id}: {r.text[:120]}")
    return "\n".join(lines)


async def promote_rule_impl(rule_id: str) -> str:
    store = _default_store()
    loaded = store.load()
    for r in loaded.accepted:
        if r.id == rule_id:
            try:
                store.update_tier(rule_id, new_tier="core")
            except Exception as e:
                return f"Could not promote {rule_id}: {e}"
            audit_log.append_event(
                kind="tier_transition", rule_id=rule_id,
                from_tier="accepted", to_tier="core",
                reason="user voice promote",
            )
            return f"Promoted {rule_id} to core."
    return f"Rule {rule_id} not eligible (must be in accepted tier)."


@function_tool
async def evolution_status() -> str:
    """Counts of rules in each tier of the learned-rules store.

    Use when the user asks:
      - "what's the evolution status"
      - "how many learned rules do we have"
      - "any new rules"
    """
    return await evolution_status_impl()


@function_tool
async def evolution_report(when: str = "today") -> str:
    """Read the daily evolution report aloud.

    Use when the user asks:
      - "today's evolution report"
      - "what changed today"
      - "this week's evolution"
    """
    return await evolution_report_impl(when)


@function_tool
async def revert_rule(query: str) -> str:
    """Demote a learned rule to archived by fuzzy text match.

    Anchor-tier rules are NEVER findable by this tool — those edits
    go through commit + review. Use when the user says:
      - "revert the rule about <topic>"
      - "remove the rule about <topic>"
      - "undo the chrome rule"

    Args:
        query: text fragment from the rule to remove.
    """
    return await revert_rule_impl(query)


@function_tool
async def review_staged_rules() -> str:
    """List staged rules with IDs so the user can decide which to keep.

    Use when the user asks:
      - "review staged rules"
      - "what rules are on probation"
      - "what's the staging queue"
    """
    return await review_staged_rules_impl()


@function_tool
async def promote_rule(rule_id: str) -> str:
    """Promote an accepted rule to core. User-gated by design.

    Use when the user explicitly says:
      - "promote R-0123 to core"
      - "make rule R-0042 permanent"

    Args:
        rule_id: the rule's R-NNNN identifier.
    """
    return await promote_rule_impl(rule_id)
