"""Tests for the golden canonical-response eval runner."""
from __future__ import annotations

import json
from pathlib import Path


def _write_golden_set(path: Path, items: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(i) for i in items) + "\n")


def test_runner_scores_signature_reflex_by_exact_match(tmp_path, monkeypatch):
    from pipeline.evolution import golden_eval

    items = [
        {"id": "g-1", "category": "signature_reflex",
         "user_text": "Jarvis", "expected_exact": "Yes?",
         "expected_judge_rubric": "must be 'Yes?'"},
    ]
    p = tmp_path / "golden.jsonl"
    _write_golden_set(p, items)
    monkeypatch.setattr(golden_eval, "GOLDEN_SET_PATH", p)
    monkeypatch.setattr(
        golden_eval, "_render_with_rules", lambda user_text, rules: "Yes?",
    )
    monkeypatch.setattr(
        golden_eval, "_judge_quality",
        lambda user_text, response, rubric: True,
    )

    report = golden_eval.run(rules=[])

    assert report["signature_reflex_pass_rate"] == 1.0
    assert report["judge_pass_rate"] == 1.0
    assert report["total"] == 1


def test_runner_fails_when_signature_reflex_wrong(tmp_path, monkeypatch):
    from pipeline.evolution import golden_eval

    items = [
        {"id": "g-1", "category": "signature_reflex",
         "user_text": "Jarvis", "expected_exact": "Yes?",
         "expected_judge_rubric": "must be 'Yes?'"},
    ]
    p = tmp_path / "golden.jsonl"
    _write_golden_set(p, items)
    monkeypatch.setattr(golden_eval, "GOLDEN_SET_PATH", p)
    monkeypatch.setattr(
        golden_eval, "_render_with_rules", lambda user_text, rules: "Yes, sir?",
    )
    monkeypatch.setattr(
        golden_eval, "_judge_quality",
        lambda user_text, response, rubric: True,
    )

    report = golden_eval.run(rules=[])
    assert report["signature_reflex_pass_rate"] < 1.0


def test_promotion_eligible_requires_both_thresholds(tmp_path, monkeypatch):
    from pipeline.evolution import golden_eval

    reports = [
        {"signature_reflex_pass_rate": 0.96, "judge_pass_rate": 0.86},
        {"signature_reflex_pass_rate": 0.94, "judge_pass_rate": 0.86},
        {"signature_reflex_pass_rate": 0.96, "judge_pass_rate": 0.80},
    ]
    assert golden_eval.promotion_eligible(reports[0]) is True
    assert golden_eval.promotion_eligible(reports[1]) is False
    assert golden_eval.promotion_eligible(reports[2]) is False
