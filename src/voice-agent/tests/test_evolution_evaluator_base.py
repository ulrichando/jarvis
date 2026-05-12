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


def test_judge_call_retries_on_429(monkeypatch):
    """Pre-fix: a single 429 → entire stage fails. Post-fix: backoff
    + retry up to 3 attempts on 429."""
    from pipeline.evolution.evaluator import judge_call
    import urllib.error

    calls = {"n": 0}

    def fake_urlopen(req, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            raise urllib.error.HTTPError(
                url="https://api.anthropic.com/v1/messages",
                code=429, msg="Rate limited", hdrs={"Retry-After": "0"}, fp=None,
            )
        # 3rd attempt succeeds
        import io, json
        body = json.dumps({"content": [{"text": "ok"}]}).encode()
        resp = io.BytesIO(body)
        resp.__enter__ = lambda self: self
        resp.__exit__ = lambda self, *a: False
        return resp

    monkeypatch.setattr(
        judge_call.urllib.request, "urlopen", fake_urlopen
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(judge_call, "_BACKOFF_SLEEP", lambda s: None)

    out = judge_call.judge_call("claude-sonnet-4-6", "rate this")
    assert out == "ok"
    assert calls["n"] == 3


def test_judge_call_gives_up_after_three_429s(monkeypatch):
    from pipeline.evolution.evaluator import judge_call
    import urllib.error

    calls = {"n": 0}

    def fake_urlopen(req, *args, **kwargs):
        calls["n"] += 1
        raise urllib.error.HTTPError(
            url="https://api.anthropic.com/v1/messages",
            code=429, msg="Rate limited", hdrs={}, fp=None,
        )

    monkeypatch.setattr(judge_call.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(judge_call, "_BACKOFF_SLEEP", lambda s: None)

    import pytest
    with pytest.raises(judge_call.JudgeError):
        judge_call.judge_call("claude-sonnet-4-6", "x")
    assert calls["n"] == 3  # 3 attempts total


def test_judge_call_does_not_retry_4xx_other_than_429(monkeypatch):
    from pipeline.evolution.evaluator import judge_call
    import urllib.error

    calls = {"n": 0}

    def fake_urlopen(req, *args, **kwargs):
        calls["n"] += 1
        raise urllib.error.HTTPError(
            url="https://api.anthropic.com/v1/messages",
            code=400, msg="Bad Request", hdrs={}, fp=None,
        )

    monkeypatch.setattr(judge_call.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(judge_call, "_BACKOFF_SLEEP", lambda s: None)

    import pytest
    with pytest.raises(judge_call.JudgeError):
        judge_call.judge_call("claude-sonnet-4-6", "x")
    assert calls["n"] == 1  # no retry on 400


def test_judge_call_respects_retry_after_header(monkeypatch):
    from pipeline.evolution.evaluator import judge_call
    import urllib.error

    sleeps: list[float] = []
    monkeypatch.setattr(judge_call, "_BACKOFF_SLEEP", lambda s: sleeps.append(s))

    calls = {"n": 0}

    def fake_urlopen(req, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.HTTPError(
                url="x", code=429, msg="rl", hdrs={"Retry-After": "2"}, fp=None,
            )
        import io, json
        body = json.dumps({"content": [{"text": "ok"}]}).encode()
        resp = io.BytesIO(body)
        resp.__enter__ = lambda self: self
        resp.__exit__ = lambda self, *a: False
        return resp

    monkeypatch.setattr(judge_call.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    out = judge_call.judge_call("claude-sonnet-4-6", "x")
    assert out == "ok"
    # First sleep should be ≥2 (the Retry-After value).
    assert sleeps and sleeps[0] >= 2.0
