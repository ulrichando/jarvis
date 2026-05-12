"""Tests for Stage 5 — 3-of-3 unanimous PoLL ensemble vote."""
from __future__ import annotations

import pytest


@pytest.fixture
def patch_poll(monkeypatch):
    from pipeline.evolution.evaluator import poll_ensemble

    def make(responses):
        idx = {"i": 0}

        def fake(model, prompt, *, max_tokens=400):
            i = idx["i"]
            idx["i"] += 1
            return responses[i]
        monkeypatch.setattr(poll_ensemble, "judge_call", fake)
    return make


def test_unanimous_pass(patch_poll):
    from pipeline.evolution.evaluator.poll_ensemble import poll_ensemble_stage

    good = '{"aligned_with_user_pattern": 5, "generalizable": 4, "persona_safe": 5}'
    patch_poll([good, good, good])
    r = poll_ensemble_stage({"rule": "test rule"})
    assert r.passed is True


def test_one_dissent_fails(patch_poll):
    from pipeline.evolution.evaluator.poll_ensemble import poll_ensemble_stage

    good = '{"aligned_with_user_pattern": 5, "generalizable": 4, "persona_safe": 5}'
    bad = '{"aligned_with_user_pattern": 2, "generalizable": 3, "persona_safe": 4}'
    patch_poll([good, bad, good])
    r = poll_ensemble_stage({"rule": "test rule"})
    assert r.passed is False


def test_judge_failure_degrades_to_two_of_two(patch_poll):
    from pipeline.evolution.evaluator import poll_ensemble
    from pipeline.evolution.evaluator.poll_ensemble import poll_ensemble_stage

    good = '{"aligned_with_user_pattern": 5, "generalizable": 4, "persona_safe": 5}'
    seq = iter([poll_ensemble.JudgeError("breaker open"), good, good])

    def fake(model, prompt, *, max_tokens=400):
        item = next(seq)
        if isinstance(item, Exception):
            raise item
        return item
    import pipeline.evolution.evaluator.poll_ensemble as pe
    pe.judge_call = fake

    r = poll_ensemble_stage({"rule": "test rule"})
    assert r.passed is True
    assert r.detail.get("votes_counted") == 2


def test_all_judges_fail_routes_to_hitl(patch_poll):
    from pipeline.evolution.evaluator import poll_ensemble
    from pipeline.evolution.evaluator.poll_ensemble import poll_ensemble_stage

    def fake(model, prompt, *, max_tokens=400):
        raise poll_ensemble.JudgeError("down")
    import pipeline.evolution.evaluator.poll_ensemble as pe
    pe.judge_call = fake

    r = poll_ensemble_stage({"rule": "test rule"})
    assert r.passed is False


def test_archival_skips_poll(patch_poll):
    from pipeline.evolution.evaluator.poll_ensemble import poll_ensemble_stage

    patch_poll([])
    r = poll_ensemble_stage(
        {"kind": "archive_duplicate", "target_id": "R-1"}
    )
    assert r.passed is True
