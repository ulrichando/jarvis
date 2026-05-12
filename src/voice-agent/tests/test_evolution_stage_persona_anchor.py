"""Tests for Stage 2 — Persona-anchor protection."""
from __future__ import annotations

import pytest


@pytest.fixture
def patch_judge(monkeypatch):
    from pipeline.evolution.evaluator import persona_anchor
    calls = []

    def make(response_text):
        def fake(model, prompt, *, max_tokens=600):
            calls.append((model, prompt))
            return response_text
        monkeypatch.setattr(persona_anchor, "judge_call", fake)
    return make, calls


def test_anchor_keyword_match_fails_without_llm(patch_judge):
    from pipeline.evolution.evaluator.persona_anchor import persona_anchor_stage

    make_judge, calls = patch_judge
    make_judge('{"is_persona": false, "contradicts_anchor": false, "reason": "ok"}')

    p = {"rule": "Always answer 'Yes, sir?' to bare Jarvis pings."}
    r = persona_anchor_stage(p)
    assert r.passed is False
    assert "anchor" in r.reason.lower()
    assert calls == []


def test_non_persona_rule_passes(patch_judge):
    from pipeline.evolution.evaluator.persona_anchor import persona_anchor_stage

    make_judge, _ = patch_judge
    make_judge('{"is_persona": false, "contradicts_anchor": false, "reason": "operational"}')

    p = {"rule": "When user says Chrome, launch google-chrome with --profile-directory=Default."}
    r = persona_anchor_stage(p)
    assert r.passed is True


def test_llm_classifies_as_persona_routes_to_hitl(patch_judge):
    from pipeline.evolution.evaluator.persona_anchor import persona_anchor_stage

    make_judge, _ = patch_judge
    make_judge('{"is_persona": true, "contradicts_anchor": false, "reason": "changes voice tone"}')

    p = {"rule": "Speak in a French accent."}
    r = persona_anchor_stage(p)
    assert r.passed is False
    assert "persona" in r.reason.lower()
    assert r.detail.get("route") == "HITL"


def test_judge_failure_routes_to_hitl_conservatively(patch_judge):
    from pipeline.evolution.evaluator import persona_anchor

    def fail(model, prompt, *, max_tokens=600):
        raise persona_anchor.JudgeError("network down")
    import pipeline.evolution.evaluator.persona_anchor as pa
    pa.judge_call = fail

    r = persona_anchor.persona_anchor_stage({"rule": "test rule"})
    assert r.passed is False
    assert "judge" in r.reason.lower()
