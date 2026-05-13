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

from . import audit_log, changelog
from .schema import Rule
from .store import AnchorWriteRefused, RuleStore

from pipeline.hooks import fire_hook_sync


__all__ = [
    "auto_stage",
    "rollback",
    "record_negative_signal",
    "apply_archival_proposals",
    "promote_eligible_staged",
    "propose_core_promotion",
    "BULK_RETIREMENT_THRESHOLD",
]


logger = logging.getLogger("jarvis.evolution.lifecycle")


BULK_RETIREMENT_THRESHOLD: int = 5
NEGATIVE_SIGNAL_QUARANTINE_THRESHOLD: int = 3


_negative_counts: Counter[str] = Counter()
_rebuilt_flag: bool = False


def _ensure_negative_counts_rebuilt() -> None:
    """Lazy-rebuild the in-memory negative_signal counter from the
    audit log on first call. Idempotent — flag set on first run.

    Replays negative_signal events to restore per-rule counts that
    were lost on agent restart. tier_transition events that archive
    a rule (quarantine / rollback) reset its count to 0.

    Never raises. The audit log is expected to be present + valid;
    a missing or malformed file leaves the counter empty.
    """
    global _rebuilt_flag
    if _rebuilt_flag:
        return
    _rebuilt_flag = True
    try:
        if not audit_log.LOG_PATH.exists():
            return
        text = audit_log.LOG_PATH.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning(f"[lifecycle] negative_counts rebuild read failed: {e}")
        return
    import json as _json
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = _json.loads(line)
        except _json.JSONDecodeError:
            continue
        rid = ev.get("rule_id")
        if not rid:
            continue
        kind = ev.get("kind")
        if kind == "negative_signal":
            count = int(ev.get("count", 0) or 0)
            if count:
                _negative_counts[rid] = count
        elif kind == "tier_transition" and ev.get("to_tier") == "archived":
            _negative_counts.pop(rid, None)
    logger.info(
        f"[lifecycle] rebuilt negative_counts: "
        f"{len(_negative_counts)} active rule(s)"
    )


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
    changelog.append(
        action="auto-staged",
        rule_id=rule_id,
        rule_text=rule.text,
        source=proposal.get("source"),
        reason="passed 5-stage evaluator → entered 7-day shadow",
        evidence_turns=rule.turns,
    )
    fire_hook_sync("evolution_tier_transition", {
        "action": "auto-staged",
        "rule_id": rule_id,
        "from_tier": "proposed",
        "to_tier": "staged",
        "source": proposal.get("source"),
        "rule_text": rule.text,
    })
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
    # writes the archived metadata (retired, reason). NOTE: since I-4's
    # lock-then-reread, update_tier replaces the in-store cache with a
    # fresh snapshot whose Rule objects differ in identity from `target`
    # above. Re-fetch the freshly-parsed object so save_rule's tier
    # routing sees tier="archived" and the retired/reason mutations
    # land on the right object.
    store.update_tier(rule_id, new_tier="archived")
    refreshed = store.load()
    archived_target = next(
        (r for r in refreshed.archived if r.id == rule_id), None
    )
    if archived_target is None:  # pragma: no cover - update_tier just placed it
        logger.warning(
            f"[lifecycle] rollback: {rule_id} missing from archived after "
            "update_tier; aborting metadata write"
        )
        return
    archived_target.retired = _today()
    archived_target.reason = retirement_reason
    store.save_rule(archived_target)
    audit_log.append_event(
        kind="tier_transition",
        rule_id=rule_id,
        from_tier=from_tier,
        to_tier="archived",
        reason=reason,
    )
    changelog.append(
        action="archived",
        rule_id=rule_id,
        rule_text=archived_target.text,
        reason=reason,
        extras={"from_tier": from_tier, "retirement_reason": retirement_reason},
    )
    fire_hook_sync("evolution_tier_transition", {
        "action": "archived",
        "rule_id": rule_id,
        "from_tier": from_tier,
        "to_tier": "archived",
        "reason": reason,
        "retirement_reason": retirement_reason,
        "rule_text": archived_target.text,
    })


def record_negative_signal(
    store: RuleStore, *, rule_id: str, turn_id: str,
) -> None:
    _ensure_negative_counts_rebuilt()
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


from datetime import date, datetime, timedelta

STAGED_SHADOW_DAYS = 7
ACCEPTED_REINFORCEMENT_DAYS = 30
ACCEPTED_REINFORCEMENT_COUNT = 10


def _parse_date(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def promote_eligible_staged(
    store: RuleStore, *, today: Optional[str] = None,
) -> int:
    from . import golden_eval

    today_date = _parse_date(today) or datetime.utcnow().date()
    loaded = store.load()
    if not loaded.staged:
        return 0

    report = golden_eval.run(
        rules=loaded.anchor + loaded.core + loaded.accepted + loaded.staged
    )
    if not golden_eval.promotion_eligible(report):
        audit_log.append_event(
            kind="promotion_blocked",
            reason="golden eval below threshold",
            signature_reflex_pass_rate=report.get("signature_reflex_pass_rate"),
            judge_pass_rate=report.get("judge_pass_rate"),
        )
        logger.info("[lifecycle] golden eval below threshold; no promotions")
        return 0

    promoted = 0
    for r in list(loaded.staged):
        created = _parse_date(r.created)
        if not created:
            continue
        if (today_date - created).days < STAGED_SHADOW_DAYS:
            continue
        clean = Rule(
            id=r.id, tier="accepted",
            text=r.text.replace("[STAGED] ", "", 1) if r.text.startswith("[STAGED] ") else r.text,
            created=r.created, reinforced=today_date.isoformat(),
            turns=r.turns, supersedes=r.supersedes, proposal=r.proposal,
            evidence=r.evidence,
        )
        # save_rule only removes from the destination bucket, so move the
        # rule first then overwrite the metadata in the accepted bucket.
        store.update_tier(r.id, new_tier="accepted")
        store.save_rule(clean)
        audit_log.append_event(
            kind="tier_transition",
            rule_id=r.id, from_tier="staged", to_tier="accepted",
            reason=f"{STAGED_SHADOW_DAYS}d shadow + golden eval pass",
        )
        changelog.append(
            action="promoted-to-accepted",
            rule_id=r.id,
            rule_text=clean.text,
            reason=f"{STAGED_SHADOW_DAYS}d shadow + golden eval passed",
            evidence_turns=clean.turns,
        )
        fire_hook_sync("evolution_tier_transition", {
            "action": "promoted-to-accepted",
            "rule_id": r.id,
            "from_tier": "staged",
            "to_tier": "accepted",
            "reason": f"{STAGED_SHADOW_DAYS}d shadow + golden eval passed",
            "rule_text": clean.text,
        })
        promoted += 1
    logger.info(f"[lifecycle] promoted {promoted} staged → accepted")
    return promoted


def propose_core_promotion(
    store: RuleStore, *, reinforcement_counts: dict[str, int],
    today: Optional[str] = None,
) -> list[str]:
    today_date = _parse_date(today) or datetime.utcnow().date()
    loaded = store.load()
    eligible: list[str] = []
    for r in loaded.accepted:
        created = _parse_date(r.created)
        if not created:
            continue
        if (today_date - created).days < ACCEPTED_REINFORCEMENT_DAYS:
            continue
        if reinforcement_counts.get(r.id, 0) < ACCEPTED_REINFORCEMENT_COUNT:
            continue
        eligible.append(r.id)
        audit_log.append_event(
            kind="core_promotion_proposed",
            rule_id=r.id,
            reinforcement_count=reinforcement_counts.get(r.id, 0),
            age_days=(today_date - created).days,
        )
    return eligible
