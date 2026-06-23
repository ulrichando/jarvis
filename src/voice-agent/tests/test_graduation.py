"""Tests for autonomy graduation (sub-project D, 2026-06-23).

Focus: the safety default (maybe_auto_deploy holds unless explicitly enabled)
and the eligibility scoring. Never let a test actually deploy.
"""
from __future__ import annotations

import json

from pipeline.automod import graduation


def test_default_stage_is_human_review(monkeypatch):
    monkeypatch.delenv("JARVIS_EVOLUTION_AUTONOMY_STAGE", raising=False)
    assert graduation.current_stage() == "human_review"


def test_maybe_auto_deploy_holds_by_default(monkeypatch):
    monkeypatch.delenv("JARVIS_EVOLUTION_AUTONOMY_STAGE", raising=False)
    monkeypatch.delenv("JARVIS_EVOLUTION_AUTODEPLOY", raising=False)
    out = graduation.maybe_auto_deploy("automod-x")
    assert out["action"] == "hold"
    assert "human_review" in out["reason"]


def test_maybe_auto_deploy_holds_when_stage_set_but_autodeploy_off(monkeypatch):
    monkeypatch.setenv("JARVIS_EVOLUTION_AUTONOMY_STAGE", "canary")
    monkeypatch.delenv("JARVIS_EVOLUTION_AUTODEPLOY", raising=False)
    out = graduation.maybe_auto_deploy("automod-x")
    assert out["action"] == "hold"  # second gate stops it


def test_proposal_risk_low_for_small_prompt_only_diff():
    art = {"files_changed": ["src/voice-agent/prompts/supervisor.md"], "diff": "\n" * 10}
    assert graduation.proposal_risk(art) == "low"


def test_proposal_risk_high_for_code_change():
    art = {"files_changed": ["src/voice-agent/pipeline/turn_router.py"], "diff": "\n" * 10}
    assert graduation.proposal_risk(art) == "high"


def test_proposal_risk_high_for_large_diff():
    art = {"files_changed": ["src/voice-agent/prompts/supervisor.md"], "diff": "\n" * 200}
    assert graduation.proposal_risk(art) == "high"


def test_evaluate_scores_criteria(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.delenv("JARVIS_EVOLUTION_AUTONOMY_STAGE", raising=False)
    monkeypatch.setattr(graduation, "_latest_fitness", lambda: (0.85, "up"))
    amods = tmp_path / "auto-mods"
    amods.mkdir(parents=True)
    # 6 finalized, all passed (pending/merged) → green history met; 4 merged.
    for i in range(4):
        (amods / f"automod-m{i}.json").write_text(json.dumps(
            {"id": f"m{i}", "status": "merged", "files_changed": []}))
    for i in range(2):
        (amods / f"automod-p{i}.json").write_text(json.dumps(
            {"id": f"p{i}", "status": "pending", "files_changed": []}))
    ev = graduation.evaluate()
    assert ev["stage"] == "human_review"
    assert ev["total"] == 5
    by_id = {c["id"]: c for c in ev["criteria"]}
    assert by_id["green_history"]["met"] is True       # 6/6 passed
    assert by_id["no_rollbacks"]["met"] is True         # no evolution_log → 0
    assert by_id["correct_approvals"]["met"] is True    # 4 merged, 0 reverted
    assert by_id["fitness"]["met"] is True              # mocked 0.85 up
    assert ev["met_count"] == ev["total"]
    assert ev["eligible_for_next"] is True
