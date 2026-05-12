"""Tests for the evaluator base + judge adapter."""
from __future__ import annotations

import pytest


def test_evaluator_result_pass_carries_reason():
    from pipeline.evolution.evaluator.base import EvaluatorResult

    r = EvaluatorResult(stage="provenance", passed=True, reason="ok", detail={"x": 1})
    assert r.passed
    assert r.detail["x"] == 1


def test_pipeline_short_circuits_on_first_failure():
    from pipeline.evolution.evaluator import EvaluatorPipeline, EvaluatorResult

    calls: list[str] = []

    def s1(p):
        calls.append("s1")
        return EvaluatorResult(stage="s1", passed=False, reason="boom")

    def s2(p):
        calls.append("s2")
        return EvaluatorResult(stage="s2", passed=True, reason="ok")

    pipeline = EvaluatorPipeline(stages=[s1, s2])
    results = pipeline.run({"rule": "test", "evidence_turns": ["t-1"]})

    assert calls == ["s1"]
    assert len(results) == 1
    assert results[0].passed is False


def test_pipeline_runs_all_stages_when_all_pass():
    from pipeline.evolution.evaluator import EvaluatorPipeline, EvaluatorResult

    def s(name):
        return lambda p: EvaluatorResult(stage=name, passed=True, reason="ok")

    pipeline = EvaluatorPipeline(stages=[s("a"), s("b"), s("c")])
    results = pipeline.run({"rule": "t"})
    assert [r.stage for r in results] == ["a", "b", "c"]
    assert all(r.passed for r in results)


def test_judge_call_returns_string_for_known_model(monkeypatch):
    from pipeline.evolution.evaluator import judge_call

    monkeypatch.setattr(
        judge_call,
        "_call_anthropic",
        lambda model, prompt, max_tokens: "verdict text",
    )
    out = judge_call.judge_call("claude-sonnet-4-6", "rate this")
    assert out == "verdict text"


def test_judge_call_raises_on_unknown_model():
    from pipeline.evolution.evaluator import judge_call

    with pytest.raises(ValueError):
        judge_call.judge_call("nonexistent-model-7", "x")
