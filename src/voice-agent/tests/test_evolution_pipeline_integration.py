"""End-to-end test of the 5-stage evaluator with mocked judges."""
from __future__ import annotations

import json


def test_proposal_passes_all_five_stages(monkeypatch):
    from pipeline.evolution.evaluator import (
        build_default_pipeline, persona_anchor, replay_delta,
        red_team, poll_ensemble,
    )

    monkeypatch.setattr(
        persona_anchor, "judge_call",
        lambda model, prompt, *, max_tokens=600: json.dumps(
            {"is_persona": False, "contradicts_anchor": False, "reason": "ok"}
        ),
    )
    monkeypatch.setattr(
        replay_delta, "_sample_historical_turns",
        lambda n: [{"id": f"t-{i}", "user_text": f"q{i}",
                    "jarvis_text": f"a{i}", "route": "TASK"}
                   for i in range(5)],
    )
    monkeypatch.setattr(
        replay_delta, "_render_response", lambda t, r, with_rule: f"resp-{with_rule}"
    )
    monkeypatch.setattr(
        replay_delta, "_judge_pair",
        lambda *args, **kwargs: "improved",
    )
    monkeypatch.setattr(
        red_team, "_generate_probes",
        lambda rule, n: [f"probe {i}" for i in range(10)],
    )
    monkeypatch.setattr(
        red_team, "_supervisor_refuses_probe", lambda probe, rule: False
    )
    monkeypatch.setattr(
        poll_ensemble, "judge_call",
        lambda model, prompt, *, max_tokens=400: json.dumps(
            {"aligned_with_user_pattern": 5,
             "generalizable": 4, "persona_safe": 5}
        ),
    )

    pipeline = build_default_pipeline()
    results = pipeline.run({
        "source": "batch_miner",
        "rule": "Use --profile-directory=Default with Chrome.",
        "evidence_turns": ["t-1", "t-2", "t-3"],
    })
    assert len(results) == 5
    assert all(r.passed for r in results)


def test_proposal_short_circuits_on_persona_fail(monkeypatch):
    from pipeline.evolution.evaluator import build_default_pipeline

    pipeline = build_default_pipeline()
    results = pipeline.run({
        "source": "batch_miner",
        "rule": "Always say 'Yes, sir?' to Jarvis pings.",
        "evidence_turns": ["t-1", "t-2", "t-3"],
    })

    assert len(results) == 2
    assert results[0].stage == "provenance"
    assert results[0].passed is True
    assert results[1].stage == "persona_anchor"
    assert results[1].passed is False
