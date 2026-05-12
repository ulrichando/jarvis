"""Tests for Stage 4 — Behavioral red-team."""
from __future__ import annotations

import pytest


@pytest.fixture
def patch_redteam(monkeypatch):
    from pipeline.evolution.evaluator import red_team

    def make(generated_probes, refusals):
        monkeypatch.setattr(
            red_team, "_generate_probes",
            lambda rule, n: generated_probes,
        )
        idx = {"i": 0}

        def fake_check(probe, rule):
            i = idx["i"]
            idx["i"] += 1
            return refusals[i] if i < len(refusals) else False
        monkeypatch.setattr(red_team, "_supervisor_refuses_probe", fake_check)
    return make


def test_no_overcorrection_passes(patch_redteam):
    from pipeline.evolution.evaluator.red_team import red_team_stage

    patch_redteam(
        generated_probes=[f"probe {i}" for i in range(10)],
        refusals=[False] * 10,
    )
    r = red_team_stage({"rule": "don't open chromium"})
    assert r.passed is True
    assert r.detail.get("probes") == 10


def test_overcorrection_fails(patch_redteam):
    from pipeline.evolution.evaluator.red_team import red_team_stage

    refusals = [False] * 5 + [True] + [False] * 4
    patch_redteam(
        generated_probes=[f"probe {i}" for i in range(10)],
        refusals=refusals,
    )
    r = red_team_stage({"rule": "don't open chromium"})
    assert r.passed is False
    assert "probe 5" in r.detail.get("triggering_probe", "") \
        or "probe" in r.reason.lower()


def test_archival_skips_red_team(patch_redteam):
    from pipeline.evolution.evaluator.red_team import red_team_stage

    patch_redteam(generated_probes=[], refusals=[])
    r = red_team_stage(
        {"kind": "archive_duplicate", "target_id": "R-1"}
    )
    assert r.passed is True
