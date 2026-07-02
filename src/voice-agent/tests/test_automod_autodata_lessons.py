"""AutoData-lesson upgrades to the evolution loop (2026-07-02).

Covers: queue admission (paraphrase dedup, self-loop → needs-human, retry
exemption, false-positive guards), gate-feedback threading in retry briefs,
the failure-log digest, learnability-weighted axis selection, and latency
attribution. Paper: arXiv:2606.25996.
"""
from __future__ import annotations

import json

from pipeline.automod import _state, fitness_feedback as ff, introspection, patterns


def _queue_lines():
    qp = _state.queue_path()
    if not qp.exists():
        return []
    return [json.loads(l) for l in qp.read_text(encoding="utf-8").splitlines() if l.strip()]


def _seed_queue(*records):
    qp = _state.queue_path()
    qp.parent.mkdir(parents=True, exist_ok=True)
    with qp.open("a", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


# ── queue admission ──────────────────────────────────────────────────


def test_admission_rejects_paraphrase_duplicate(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    _seed_queue({
        "kind": "self_improvement",
        "intent": "Implement sentence-boundary chunked streaming between LLM output and TTS\n\nlatency",
    })
    admit, reason = patterns.queue_admission({
        "kind": "self_improvement",
        "intent": "Implement sentence-boundary chunked streaming from LLM output directly to TTS\n\nother rationale",
    })
    assert admit is False
    assert reason == "near-duplicate"


def test_admission_rejects_self_loop_to_needs_human(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    rec = {
        "id": "automod-x",
        "kind": "self_improvement",
        "intent": "Add a hard circuit-breaker to the automod retry loop that halts after 3 failures\n\nwhy",
    }
    admit, reason = patterns.queue_admission(rec)
    assert admit is False
    assert reason == "self-loop-target"
    nh = [json.loads(l) for l in _state.needs_human_path().read_text().splitlines()]
    assert nh and nh[0]["id"] == "automod-x"
    assert nh[0]["rejected_reason"] == "self-loop-target"
    assert _queue_lines() == []  # never queued


def test_admission_exempts_retries_from_dedup(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    _seed_queue({"kind": "self_improvement", "intent": "Goal X"})
    admit, _ = patterns.queue_admission(
        {"kind": "self_improvement", "intent": "Goal X", "lineage": "automod-a", "attempt": 2})
    assert admit is True


def test_admission_allows_blocklist_mention_in_negative_instruction(tmp_path, monkeypatch):
    # The fitness confab template SAYS "Do NOT touch confab_detector.py" — a
    # negative instruction, not a self-loop goal. Must be admitted.
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    built = ff.build_intent("confab", {"latest": 0.4, "floor": 0.6, "n_below": 4, "window_m": 5})
    admit, reason = patterns.queue_admission({"kind": "fitness", "intent": built["intent"]})
    assert admit is True, reason


def test_admission_allows_artifact_id_mention_in_rationale(tmp_path, monkeypatch):
    # Citing a prior build ("automod-2026-06-25-c74ed6 attempted this") is not
    # targeting the loop — live false positive caught in the 2026-07-02 dry run.
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    admit, reason = patterns.queue_admission({
        "kind": "self_improvement",
        "intent": ("Implement chunked sentence-boundary streaming between LLM and TTS\n\n"
                   "Build automod-2026-06-25-c74ed6 attempted this but its unit tests failed."),
    })
    assert admit is True, reason


def test_admission_rejects_self_loop_named_in_body(tmp_path, monkeypatch):
    # The target may be named past the first line — still a self-loop goal.
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    admit, reason = patterns.queue_admission({
        "kind": "self_improvement",
        "intent": ("Implement pre-plan blocklist validation before any diff is generated\n\n"
                   "Wire a check into the automod planning agent."),
    })
    assert admit is False
    assert reason == "self-loop-target"


def test_admission_keeps_distinct_error_intents_despite_shared_boilerplate(tmp_path, monkeypatch):
    # error intents share their first line; kind=error skips similarity (deduped
    # upstream by recurring_errors.signature) so distinct errors never collide.
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    a = ("Investigate a recurring exception in JARVIS's own code.\n\n"
         "EXCEPTION: ValueError\nMESSAGE: 'bad int'")
    b = ("Investigate a recurring exception in JARVIS's own code.\n\n"
         "EXCEPTION: KeyError\nMESSAGE: 'missing key'")
    _seed_queue({"kind": "error", "intent": a})
    admit, reason = patterns.queue_admission({"kind": "error", "intent": b})
    assert admit is True, reason


# ── retry brief: gate feedback threading ─────────────────────────────


def test_build_retry_intent_threads_gate_and_council_feedback(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    home = _state._automod_home()
    home.mkdir(parents=True, exist_ok=True)
    (home / "automod-t1.review.json").write_text(json.dumps({
        "lenses": {"correctness": {"verdict": "concern",
                                   "findings": ["off-by-one in providers/tts.py:12"]}},
    }), encoding="utf-8")
    art = {
        "id": "automod-t1", "status": "failed", "attempt": 1,
        "intent": "Reduce TTS start latency\n\ndetails",
        "rejection_reason": "tests_failed_on_rerun",
        "stress": {"verdict": "fail", "summary": "2 stress test(s) failed on empty input"},
    }
    retry = patterns.build_retry_intent(art)
    assert retry is not None
    assert "GATE FEEDBACK" in retry["intent"]
    assert "off-by-one in providers/tts.py:12" in retry["intent"]
    assert "2 stress test(s) failed" in retry["intent"]
    assert retry["lineage"] == "automod-t1"
    assert retry["attempt"] == 2


def test_build_retry_intent_no_feedback_section_when_none(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    art = {"id": "automod-t2", "status": "failed", "attempt": 1,
           "intent": "Goal", "rejection_reason": "no_commit_landed"}
    retry = patterns.build_retry_intent(art)
    assert retry is not None
    assert "GATE FEEDBACK" not in retry["intent"]


# ── failure digest ───────────────────────────────────────────────────


def test_extract_failure_digest_structures_the_evidence():
    failed = [
        {"id": "a", "reason": "blocked_path:src/voice-agent/pipeline/automod/cycle.py",
         "intent": "Fix the automod orchestrator retry loop", "attempt": 1, "log_tail": ""},
        {"id": "b", "reason": "plan_rejected: plan touches a blocklisted path: "
                              "src/voice-agent/pipeline/automod/cycle.py",
         "intent": "Improve the evolution pipeline scheduling", "attempt": 1, "log_tail": ""},
        {"id": "c", "reason": "tests_failed_on_rerun",
         "intent": "Speed up prompts/supervisor.md assembly", "attempt": 2,
         "log_tail": "..." + "\nAssertionError: boom on empty ctx\nok line\n"},
        {"id": "d", "reason": "no_commit_landed", "intent": "Tune VAD floor",
         "attempt": 1, "log_tail": ""},
    ]
    digest = introspection.extract_failure_digest(failed)
    assert digest["n_failed"] == 4
    assert digest["by_class"]["blocklist"] == 2
    assert digest["by_class"]["tests_failed"] == 1
    assert digest["by_class"]["no_commit"] == 1
    assert digest["repeated_target_paths"][0] == {
        "path": "src/voice-agent/pipeline/automod/cycle.py", "count": 2}
    assert digest["self_loop_targets"] == 2  # automod orchestrator + evolution pipeline
    assert any("AssertionError" in l for l in digest["sample_error_lines"])


def test_gather_failure_digest_reads_artifacts_and_logs(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    home = _state._automod_home()
    home.mkdir(parents=True, exist_ok=True)
    (home / "automod-f1.json").write_text(json.dumps({
        "id": "automod-f1", "status": "failed", "intent": "Goal one",
        "rejection_reason": "base_suite_red", "attempt": 1}), encoding="utf-8")
    (home / "automod-f1.log").write_text("start\nFAILED tests/test_x.py::t\n", encoding="utf-8")
    (home / "automod-p1.json").write_text(json.dumps({
        "id": "automod-p1", "status": "pending", "intent": "Other"}), encoding="utf-8")
    digest = introspection.gather_failure_digest()
    assert digest is not None
    assert digest["n_failed"] == 1
    assert digest["by_class"] == {"tests_failed": 1}
    assert any("FAILED" in l for l in digest["sample_error_lines"])


# ── learnability-weighted axis selection ─────────────────────────────


def _reading(per_axis):
    return {"per_axis": per_axis, "composite": 0.7, "passed": True}


def test_weak_axis_prefers_oscillating_over_flat():
    # latency is flat at 0.40 (never responds); reask oscillates in a learnable
    # band. AutoData lesson: pick the learnable one even though it isn't lowest.
    readings = [
        _reading({"latency": 0.40, "reask": 0.50}),
        _reading({"latency": 0.40, "reask": 0.58}),
        _reading({"latency": 0.40, "reask": 0.42}),
        _reading({"latency": 0.40, "reask": 0.59}),
        _reading({"latency": 0.40, "reask": 0.45}),
    ]
    axis, evidence = ff.weak_axis(readings)
    assert axis == "reask"
    assert evidence["std"] > 0
    assert evidence["flat"] is False


def test_weak_axis_flags_flat_at_floor():
    readings = [_reading({"latency": 0.40, "reask": 0.9}) for _ in range(5)]
    axis, evidence = ff.weak_axis(readings)
    assert axis == "latency"
    assert evidence["flat"] is True


def test_build_intent_adds_flat_note():
    out = ff.build_intent("latency", {"latest": 0.4, "floor": 0.6, "n_below": 5,
                                      "window_m": 5, "std": 0.0, "flat": True})
    assert "FLAT" in out["intent"]
    assert "structurally DIFFERENT approach" in out["intent"]


# ── latency attribution ──────────────────────────────────────────────


def test_latency_attribution_splits_fallback_vs_first_try():
    rows = [(5000, 1, "deepseek-chat"), (6000, 0, "deepseek-chat"), (7000, 0, "kimi-k2.6")]
    att = ff.latency_attribution(rows)
    assert att["n_slow"] == 3
    assert att["n_fallback"] == 1
    assert att["n_first_try"] == 2
    assert att["top_slow_models"][0] == {"model": "deepseek-chat", "count": 2}


def test_latency_attribution_none_when_no_slow_turns():
    assert ff.latency_attribution([]) is None


def test_build_intent_appends_attribution():
    att = {"slow_ms": 3000, "n_slow": 10, "n_fallback": 7, "n_first_try": 3,
           "top_slow_models": [{"model": "deepseek-chat", "count": 6}]}
    out = ff.build_intent("latency", {"latest": 0.4, "floor": 0.6, "n_below": 4,
                                      "window_m": 5}, attribution=att)
    assert "LATENCY ATTRIBUTION" in out["intent"]
    assert "7 involved" in out["intent"]
    assert "deepseek-chat×6" in out["intent"]


# ── introspection paraphrase dedup end-to-end ────────────────────────


def test_enqueue_improvements_drops_paraphrase_of_built_goal(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    home = _state._automod_home()
    home.mkdir(parents=True, exist_ok=True)
    (home / "automod-old.json").write_text(json.dumps({
        "id": "old", "kind": "self_improvement", "status": "failed",
        "intent": "Implement sentence-boundary chunked streaming between LLM output and TTS\n\nr"}),
        encoding="utf-8")
    result = {"improvements": [{
        "title": "Implement sentence-boundary chunked streaming from LLM output directly to TTS",
        "rationale": "different wording, same goal", "target_axis": "latency"}]}
    assert introspection.enqueue_improvements(result) == 0


def test_enqueue_improvements_routes_self_loop_to_needs_human(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    _state._automod_home().mkdir(parents=True, exist_ok=True)
    result = {"improvements": [{
        "title": "Install a circuit-breaker in the automod orchestrator",
        "rationale": "stop retry storms", "target_axis": "none"}]}
    assert introspection.enqueue_improvements(result) == 0
    assert _state.needs_human_path().exists()
