"""Lived-experience shadow trial (2026-07-02) — the auto-promotion oracle.

Covers turn selection (informative-boundary bias + axis affinity), judge
mapping (A=baseline/B=variant → better/tie/worse), trial aggregation, the
promotion decision thresholds, and never-raises degradation. No LLM / no live
agent — every seam is injected. Paper context: DGM 2505.22954, AutoData 2606.25996.
"""
from __future__ import annotations

import json

from pipeline.automod import shadow_trial as st
from pipeline.automod.shadow_trial import TrialTurn


def _turn(id, *, user="hi", reply="ok", route="TASK", correction=False,
          fallback=False, tool_error=False, confab=False, reply_len=None):
    return TrialTurn(
        id=id, user_text=user, baseline_reply=reply, route=route,
        correction=correction, fallback=fallback, tool_error=tool_error,
        confab=confab, reply_len=reply_len if reply_len is not None else len(reply),
    )


# ── informativeness + selection ──────────────────────────────────────

def test_informativeness_ranks_boundary_over_trivial():
    trivial = _turn("a", route="BANTER")
    correction = _turn("b", correction=True)
    confab = _turn("c", confab=True)
    assert correction.informativeness() > trivial.informativeness()
    assert confab.informativeness() > trivial.informativeness()
    assert trivial.informativeness() == 0


def test_select_prefers_informative_turns():
    turns = [_turn(f"triv{i}", route="BANTER") for i in range(10)]
    turns += [_turn("corr", correction=True), _turn("confab", confab=True),
              _turn("fb", fallback=True)]
    chosen = st.select_trial_turns(turns, n=3)
    ids = {t.id for t in chosen}
    assert ids == {"corr", "confab", "fb"}  # the 3 informative ones, not the trivia


def test_select_axis_affinity_breaks_ties():
    # Two equally-informative turns; the one on the axis's route wins.
    on_axis = _turn("on", route="TASK", fallback=True)     # action → TASK
    off_axis = _turn("off", route="BANTER", fallback=True)
    chosen = st.select_trial_turns([off_axis, on_axis], target_axis="action", n=1)
    assert chosen[0].id == "on"


def test_select_falls_back_to_volume_when_few_informative():
    # Only 1 informative turn but n=5 requested → pad with the rest (a
    # non-regression check still wants volume) rather than return just 1.
    turns = [_turn("corr", correction=True)] + [_turn(f"t{i}", route="BANTER") for i in range(6)]
    chosen = st.select_trial_turns(turns, n=5)
    assert len(chosen) == 5
    assert chosen[0].id == "corr"  # informative one still ranked first


# ── judging ──────────────────────────────────────────────────────────

def test_judge_maps_B_to_better():
    j = st.judge_turn(_turn("x"), "variant reply", lambda u, a, b: '{"winner":"B","why":"clearer"}')
    assert j["outcome"] == "better"
    assert j["why"] == "clearer"


def test_judge_maps_A_to_worse():
    j = st.judge_turn(_turn("x"), "variant reply", lambda u, a, b: '{"winner":"A"}')
    assert j["outcome"] == "worse"


def test_judge_tie_and_unparseable_are_tie():
    assert st.judge_turn(_turn("x"), "v", lambda u, a, b: '{"winner":"tie"}')["outcome"] == "tie"
    assert st.judge_turn(_turn("x"), "v", lambda u, a, b: "garbage")["outcome"] == "tie"


def test_judge_survives_judge_exception():
    def boom(u, a, b):
        raise RuntimeError("api down")
    j = st.judge_turn(_turn("x"), "v", boom)
    assert j["outcome"] == "tie"
    assert "judge error" in j["why"]


def test_judge_parses_fenced_json():
    j = st.judge_turn(_turn("x"), "v", lambda u, a, b: '```json\n{"winner":"B"}\n```')
    assert j["outcome"] == "better"


# ── trial aggregation + decision ─────────────────────────────────────

def _turns(n):
    return [_turn(f"t{i}", correction=True) for i in range(n)]


def test_trial_all_better_passes():
    r = st.run_shadow_trial(_turns(6), lambda u: "better reply", lambda u, a, b: '{"winner":"B"}')
    assert r.verdict == "pass"
    assert r.better == 6 and r.worse == 0


def test_trial_any_worse_regresses():
    # 5 better, 1 worse → regressed (conservative: any regression blocks auto-deploy)
    outcomes = iter(['{"winner":"B"}'] * 5 + ['{"winner":"A"}'])
    r = st.run_shadow_trial(_turns(6), lambda u: "v", lambda u, a, b: next(outcomes))
    assert r.verdict == "regressed"
    assert r.worse == 1 and r.better == 5


def test_trial_below_min_is_skipped():
    r = st.run_shadow_trial(_turns(3), lambda u: "v", lambda u, a, b: '{"winner":"B"}')
    assert r.verdict == "skipped"
    assert "need" in r.reason


def test_trial_ties_still_pass():
    r = st.run_shadow_trial(_turns(6), lambda u: "v", lambda u, a, b: '{"winner":"tie"}')
    assert r.verdict == "pass"
    assert r.tie == 6


def test_trial_variant_error_not_counted_as_regression():
    # A variant that fails to run on some turns is skipped, not a regression.
    calls = {"n": 0}
    def flaky(u):
        calls["n"] += 1
        if calls["n"] % 2 == 0:
            raise RuntimeError("variant crashed")
        return "v"
    r = st.run_shadow_trial(_turns(8), flaky, lambda u, a, b: '{"winner":"B"}')
    # 4 ran + judged better, 4 errored (not counted). n=4 < MIN → skipped, NOT regressed.
    assert r.worse == 0
    assert r.verdict in ("skipped", "pass")
    assert any(p["outcome"] == "error" for p in r.per_turn)


def test_trial_empty_variant_reply_is_error_not_regression():
    r = st.run_shadow_trial(_turns(6), lambda u: "   ", lambda u, a, b: '{"winner":"B"}')
    assert r.worse == 0
    assert r.verdict == "skipped"  # nothing judged


def test_decide_thresholds_directly():
    assert st.decide(6, 0, 0, 6, []).verdict == "pass"
    assert st.decide(3, 2, 1, 6, []).verdict == "regressed"
    assert st.decide(2, 0, 0, 2, []).verdict == "skipped"


def test_result_to_dict_shape():
    d = st.decide(5, 1, 0, 6, [{"id": "x", "outcome": "better"}]).to_dict()
    assert d["verdict"] == "pass"
    assert set(d) >= {"verdict", "better", "tie", "worse", "n", "reason", "per_turn"}
    assert json.dumps(d)  # serializable for the artifact


# ── top-level entry: never raises, degrades cleanly ──────────────────

def test_trial_proposal_skipped_without_judge(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    out = st.trial_proposal("action", lambda u: "v", turns=_turns(6))
    assert out["verdict"] == "skipped"
    assert "judge" in out["reason"]


def test_trial_proposal_skipped_with_no_turns():
    out = st.trial_proposal("action", lambda u: "v", judge_fn=lambda u, a, b: '{"winner":"B"}', turns=[])
    assert out["verdict"] == "skipped"


def test_trial_proposal_end_to_end_with_stubs():
    out = st.trial_proposal(
        "confab",
        lambda u: "a better, evidence-grounded reply",
        judge_fn=lambda u, a, b: '{"winner":"B","why":"grounded"}',
        turns=_turns(8),
    )
    assert out["verdict"] == "pass"
    assert out["better"] == 8


def test_load_recent_turns_missing_db_returns_empty(tmp_path):
    assert st.load_recent_turns(db_path=tmp_path / "nope.db") == []
