"""Tests for the lifecycle state machine — auto-stage, rollback, quarantine."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import pytest


ANCHOR = """\
---
schema_version: 2
---

## ═══ ANCHOR ═══

- <!-- id=A-0001 tier=anchor --> Reply "Yes?".
"""


@pytest.fixture
def store(tmp_path, monkeypatch):
    from pipeline.evolution.store import RuleStore
    from pipeline.evolution import audit_log, changelog

    anchor = tmp_path / "anchor.md"
    learned = tmp_path / "learned.md"
    anchor.write_text(ANCHOR)
    sha = hashlib.sha256(ANCHOR.encode()).hexdigest()
    learned.write_text(
        f"---\nschema_version: 2\nanchor_baseline_sha256: {sha}\n---\n\n"
        "# JARVIS Learned Rules\n\n## ═══ ACCEPTED ═══\n\n"
        '- <!-- id=R-0001 tier=accepted --> Reply "Yes?" to bare Jarvis pings.\n'
    )
    monkeypatch.setattr(audit_log, "LOG_PATH", tmp_path / "audit.jsonl")
    # Redirect the human-readable changelog so test runs don't pollute
    # ~/Documents/jarvis-evolution/.
    monkeypatch.setattr(changelog, "CHANGELOG_DIR", tmp_path / "changelog")
    return RuleStore(anchor_path=anchor, learned_path=learned)


def test_auto_stage_appends_staged_rule(store):
    from pipeline.evolution import lifecycle

    proposal = {
        "source": "live_capture",
        "rule": "Don't open chromium when user says Chrome.",
        "evidence_turns": ["t-100"],
        "matched_phrase": "don't open",
    }
    rule_id = lifecycle.auto_stage(store, proposal, logging_only=False)

    loaded = store.load()
    staged_ids = [r.id for r in loaded.staged]
    assert rule_id in staged_ids
    assert any("chromium" in r.text.lower() for r in loaded.staged)


def test_logging_only_mode_does_not_write_store(store, tmp_path, monkeypatch):
    from pipeline.evolution import lifecycle, audit_log

    monkeypatch.setattr(audit_log, "LOG_PATH", tmp_path / "audit.jsonl")
    proposal = {
        "source": "live_capture",
        "rule": "Don't open chromium when user says Chrome.",
        "evidence_turns": ["t-100"],
        "matched_phrase": "don't open",
    }
    rule_id = lifecycle.auto_stage(store, proposal, logging_only=True)

    loaded = store.load()
    assert all(r.id != rule_id for r in loaded.staged)

    log_lines = (tmp_path / "audit.jsonl").read_text().splitlines()
    parsed = [json.loads(l) for l in log_lines]
    assert any(
        p.get("kind") == "would_stage" for p in parsed
    )


def test_rollback_demotes_staged_rule(store):
    from pipeline.evolution import lifecycle
    from pipeline.evolution.schema import Rule

    store.save_rule(Rule(
        id="R-0099", tier="staged", text="[STAGED] don't open chromium",
        created="2026-05-12",
    ))
    lifecycle.rollback(store, rule_id="R-0099", reason="user said no")

    loaded = store.load()
    staged_ids = [r.id for r in loaded.staged]
    archived_ids = [r.id for r in loaded.archived]
    assert "R-0099" not in staged_ids
    assert "R-0099" in archived_ids


def test_rollback_refuses_to_touch_anchor_tier(store):
    from pipeline.evolution import lifecycle
    from pipeline.evolution.store import AnchorWriteRefused

    with pytest.raises(AnchorWriteRefused):
        lifecycle.rollback(store, rule_id="A-0001", reason="trying to remove anchor")


def test_quarantine_after_three_negative_signals(store, tmp_path, monkeypatch):
    from pipeline.evolution import lifecycle, audit_log
    monkeypatch.setattr(audit_log, "LOG_PATH", tmp_path / "audit.jsonl")

    from pipeline.evolution.schema import Rule
    store.save_rule(Rule(
        id="R-0050", tier="accepted",
        text="Always use --profile-directory=Default with Chrome.",
        created="2026-05-01",
    ))

    lifecycle.record_negative_signal(store, rule_id="R-0050", turn_id="t-1")
    lifecycle.record_negative_signal(store, rule_id="R-0050", turn_id="t-2")
    lifecycle.record_negative_signal(store, rule_id="R-0050", turn_id="t-3")

    loaded = store.load()
    quarantined = [r for r in loaded.archived if r.id == "R-0050"]
    assert len(quarantined) == 1
    assert quarantined[0].reason == "quarantine_after_3_negative_signals"


def test_bulk_retirement_guard_routes_to_hitl(store):
    from pipeline.evolution import lifecycle
    from pipeline.evolution.schema import Rule

    for i in range(6):
        store.save_rule(Rule(id=f"R-{i:04d}", tier="accepted",
                             text=f"rule {i}", created="2026-05-01"))

    proposals = [
        {"source": "contradiction_detector", "kind": "archive_duplicate",
         "target_id": f"R-{i:04d}", "reason": "duplicate"}
        for i in range(6)
    ]
    routed = lifecycle.apply_archival_proposals(store, proposals)

    assert routed["auto_archived"] == 0
    assert routed["routed_to_hitl"] == 6


def test_promote_eligible_staged_to_accepted(store, monkeypatch, tmp_path):
    from pipeline.evolution import lifecycle, audit_log, golden_eval
    from pipeline.evolution.schema import Rule

    monkeypatch.setattr(audit_log, "LOG_PATH", tmp_path / "audit.jsonl")
    monkeypatch.setattr(
        golden_eval, "run", lambda rules: {
            "signature_reflex_pass_rate": 0.96,
            "judge_pass_rate": 0.86,
            "total": 50, "misses": [],
        },
    )

    old = "2026-05-01"
    store.save_rule(Rule(
        id="R-0200", tier="staged", text="[STAGED] use Default profile",
        created=old, reinforced=old,
    ))

    lifecycle.promote_eligible_staged(store, today="2026-05-12")

    loaded = store.load()
    accepted_ids = [r.id for r in loaded.accepted]
    staged_ids = [r.id for r in loaded.staged]
    assert "R-0200" in accepted_ids
    assert "R-0200" not in staged_ids


def test_recent_staged_rule_not_promoted(store, monkeypatch, tmp_path):
    from pipeline.evolution import lifecycle, audit_log, golden_eval
    from pipeline.evolution.schema import Rule

    monkeypatch.setattr(audit_log, "LOG_PATH", tmp_path / "audit.jsonl")
    monkeypatch.setattr(
        golden_eval, "run", lambda rules: {
            "signature_reflex_pass_rate": 1.0, "judge_pass_rate": 1.0,
            "total": 50, "misses": [],
        },
    )

    today = "2026-05-12"
    store.save_rule(Rule(
        id="R-0201", tier="staged", text="[STAGED] recent rule",
        created=today, reinforced=today,
    ))

    lifecycle.promote_eligible_staged(store, today=today)

    loaded = store.load()
    staged_ids = [r.id for r in loaded.staged]
    assert "R-0201" in staged_ids


def test_promotion_blocked_when_golden_eval_fails(store, monkeypatch, tmp_path):
    from pipeline.evolution import lifecycle, audit_log, golden_eval
    from pipeline.evolution.schema import Rule

    monkeypatch.setattr(audit_log, "LOG_PATH", tmp_path / "audit.jsonl")
    monkeypatch.setattr(
        golden_eval, "run", lambda rules: {
            "signature_reflex_pass_rate": 0.80,
            "judge_pass_rate": 0.90,
            "total": 50, "misses": [],
        },
    )

    old = "2026-05-01"
    store.save_rule(Rule(
        id="R-0202", tier="staged", text="[STAGED] eligible by age",
        created=old, reinforced=old,
    ))

    lifecycle.promote_eligible_staged(store, today="2026-05-12")

    loaded = store.load()
    assert any(r.id == "R-0202" for r in loaded.staged)


def test_negative_counts_rebuild_from_audit_log_across_restart(
    store, tmp_path, monkeypatch
):
    """Pre-fix: _negative_counts is in-memory only. After a restart,
    a rule that had 2 strikes goes back to 0 — quarantine is weaker
    than designed. Post-fix: rebuild from audit log on first use."""
    import json
    from pipeline.evolution import lifecycle, audit_log
    from pipeline.evolution.schema import Rule

    log_path = tmp_path / "audit.jsonl"
    monkeypatch.setattr(audit_log, "LOG_PATH", log_path)

    # Simulate a "before restart" session: 2 negative signals recorded
    # for R-0050.
    store.save_rule(Rule(id="R-0050", tier="accepted",
                          text="rule under test", created="2026-05-01"))
    lifecycle.record_negative_signal(store, rule_id="R-0050", turn_id="t-1")
    lifecycle.record_negative_signal(store, rule_id="R-0050", turn_id="t-2")
    assert lifecycle._negative_counts.get("R-0050", 0) == 2

    # Simulate a restart: wipe the in-memory counter + the rebuilt flag.
    lifecycle._negative_counts.clear()
    lifecycle._rebuilt_flag = False

    # Now the 3rd strike should still trigger quarantine because the
    # rebuild from the audit log restores the count to 2.
    lifecycle.record_negative_signal(store, rule_id="R-0050", turn_id="t-3")

    loaded = store.load()
    quarantined = [r for r in loaded.archived if r.id == "R-0050"]
    assert len(quarantined) == 1, (
        f"R-0050 should be quarantined after 3 signals across restart; "
        f"archived={[r.id for r in loaded.archived]}"
    )


def test_quarantine_resets_count_in_rebuild(store, tmp_path, monkeypatch):
    """After quarantine archives a rule, the rebuilt count for that
    rule should be 0 (because the rule is archived; subsequent strikes
    against it shouldn't accumulate)."""
    import json
    from pipeline.evolution import lifecycle, audit_log
    from pipeline.evolution.schema import Rule

    log_path = tmp_path / "audit.jsonl"
    monkeypatch.setattr(audit_log, "LOG_PATH", log_path)

    store.save_rule(Rule(id="R-0060", tier="accepted",
                          text="rule under test", created="2026-05-01"))
    lifecycle.record_negative_signal(store, rule_id="R-0060", turn_id="t-1")
    lifecycle.record_negative_signal(store, rule_id="R-0060", turn_id="t-2")
    lifecycle.record_negative_signal(store, rule_id="R-0060", turn_id="t-3")

    # Now quarantine has fired. Simulate restart.
    lifecycle._negative_counts.clear()
    lifecycle._rebuilt_flag = False

    # Force rebuild.
    lifecycle._ensure_negative_counts_rebuilt()
    # R-0060 was archived → its count should be 0 in the rebuild.
    assert lifecycle._negative_counts.get("R-0060", 0) == 0
