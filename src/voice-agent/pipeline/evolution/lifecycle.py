"""Lifecycle state machine — auto-stage, rollback, quarantine.

State transitions enforced here:
  - proposed → staged           (evaluator pass)
  - staged   → archived         (1-turn rollback OR 3 negative signals)
  - accepted → archived         (3 negative signals)
  - any      → archived (bulk)  (contradiction detector → routes to HITL
                                  if >5 in one cycle)

Anchor edits are structurally refused by the underlying RuleStore.
This module never bypasses that — it goes through store.save_rule()
and store.update_tier() exclusively.
"""
from __future__ import annotations

import logging
import time
from collections import Counter
from typing import Optional

from . import audit_log
from .schema import Rule
from .store import AnchorWriteRefused, RuleStore


__all__ = [
    "auto_stage",
    "rollback",
    "record_negative_signal",
    "apply_archival_proposals",
    "BULK_RETIREMENT_THRESHOLD",
]


logger = logging.getLogger("jarvis.evolution.lifecycle")


BULK_RETIREMENT_THRESHOLD: int = 5
NEGATIVE_SIGNAL_QUARANTINE_THRESHOLD: int = 3


_negative_counts: Counter[str] = Counter()


def _next_rule_id(store: RuleStore) -> str:
    used: set[str] = set()
    loaded = store.load()
    for r in loaded.all_rules:
        used.add(r.id)
    n = 1
    while f"R-{n:04d}" in used:
        n += 1
    return f"R-{n:04d}"


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def auto_stage(
    store: RuleStore,
    proposal: dict,
    *,
    logging_only: bool = False,
) -> str:
    rule_id = _next_rule_id(store)
    rule = Rule(
        id=rule_id,
        tier="staged",
        text=f"[STAGED] {proposal['rule']}",
        created=_today(),
        reinforced=_today(),
        turns=list(proposal.get("evidence_turns") or []),
        proposal=proposal.get("proposal_id"),
        evidence=str(proposal.get("evidence_quote") or proposal.get("pattern") or ""),
    )
    if logging_only:
        audit_log.append_event(
            kind="would_stage",
            rule_id=rule_id,
            source=proposal.get("source"),
            evidence_turns=rule.turns,
        )
        logger.info(f"[lifecycle] (logging-only) would stage {rule_id}: {rule.text[:80]}")
        return rule_id

    store.save_rule(rule)
    audit_log.append_event(
        kind="tier_transition",
        rule_id=rule_id,
        from_tier="proposed",
        to_tier="staged",
        source=proposal.get("source"),
        evidence_turns=rule.turns,
    )
    logger.info(f"[lifecycle] staged {rule_id}: {rule.text[:80]}")
    return rule_id


def rollback(
    store: RuleStore, *, rule_id: str, reason: str, retirement_reason: str = "rollback",
) -> None:
    loaded = store.load()
    for r in loaded.anchor:
        if r.id == rule_id:
            raise AnchorWriteRefused(
                f"refused to roll back anchor rule {rule_id}"
            )
    target: Optional[Rule] = None
    from_tier: Optional[str] = None
    for bucket in ("core", "accepted", "staged"):
        for r in getattr(loaded, bucket):
            if r.id == rule_id:
                target = r
                from_tier = bucket
                break
        if target:
            break
    if target is None:
        logger.warning(f"[lifecycle] rollback target {rule_id} not found")
        return
    # update_tier moves the rule between buckets; then save_rule
    # writes the archived metadata (retired, reason).
    store.update_tier(rule_id, new_tier="archived")
    target.retired = _today()
    target.reason = retirement_reason
    store.save_rule(target)
    audit_log.append_event(
        kind="tier_transition",
        rule_id=rule_id,
        from_tier=from_tier,
        to_tier="archived",
        reason=reason,
    )


def record_negative_signal(
    store: RuleStore, *, rule_id: str, turn_id: str,
) -> None:
    _negative_counts[rule_id] += 1
    audit_log.append_event(
        kind="negative_signal", rule_id=rule_id, turn_id=turn_id,
        count=_negative_counts[rule_id],
    )
    if _negative_counts[rule_id] >= NEGATIVE_SIGNAL_QUARANTINE_THRESHOLD:
        try:
            rollback(
                store,
                rule_id=rule_id,
                reason=f"{_negative_counts[rule_id]} negative signals",
                retirement_reason="quarantine_after_3_negative_signals",
            )
        except AnchorWriteRefused:
            pass
        _negative_counts.pop(rule_id, None)


def apply_archival_proposals(
    store: RuleStore, proposals: list[dict],
) -> dict:
    auto_archived = 0
    routed_to_hitl = 0
    if len(proposals) > BULK_RETIREMENT_THRESHOLD:
        for p in proposals:
            audit_log.append_event(
                kind="archival_routed_to_hitl",
                target_id=p.get("target_id"),
                kind_of_archival=p.get("kind"),
                reason=p.get("reason"),
            )
            routed_to_hitl += 1
        return {"auto_archived": auto_archived, "routed_to_hitl": routed_to_hitl}

    for p in proposals:
        target_id = p.get("target_id")
        if not target_id:
            continue
        try:
            rollback(
                store,
                rule_id=target_id,
                reason=p.get("reason", "archival"),
                retirement_reason=p.get("reason", "archived"),
            )
            auto_archived += 1
        except AnchorWriteRefused:
            routed_to_hitl += 1
    return {"auto_archived": auto_archived, "routed_to_hitl": routed_to_hitl}
