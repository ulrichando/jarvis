"""Stress-test gate (2026-06-26). The gate logic is tested via dependency
injection — no real LLM call, no real pytest run. The reliability contract:
a generated test that FAILS rejects the change; one that ERRORS only skips
(never a false-reject)."""
from __future__ import annotations

import pytest

from pipeline.automod import stress_gate as sg


@pytest.fixture
def armed(monkeypatch):
    """Gate enabled + a key present, so we exercise the real path."""
    monkeypatch.setenv("JARVIS_AUTOMOD_STRESS_GATE", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")


def test_disabled_by_default_skips(monkeypatch):
    monkeypatch.delenv("JARVIS_AUTOMOD_STRESS_GATE", raising=False)
    assert sg.run_stress_gate("id", "diff", "intent")["verdict"] == "skipped"


def test_no_key_skips(monkeypatch):
    monkeypatch.setenv("JARVIS_AUTOMOD_STRESS_GATE", "1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert sg.run_stress_gate("id", "diff", "intent")["verdict"] == "skipped"


def test_failing_stress_test_rejects(armed):
    out = sg.run_stress_gate(
        "id", "diff", "intent",
        generate=lambda d, i: "def test_x():\n    assert False\n",
        run_tests=lambda code, aid: {"passed": 0, "failed": 1, "errored": 0, "tail": "1 failed"},
    )
    assert out["verdict"] == "fail"
    assert out["failed"] == 1


def test_erroring_generated_test_skips_not_rejects(armed):
    # A generated test that only ERRORS (bad import/collection) is the LLM's
    # fault, not a real edge-case break → skip, never a false-reject.
    out = sg.run_stress_gate(
        "id", "diff", "intent",
        generate=lambda d, i: "import nonexistent_module\n",
        run_tests=lambda code, aid: {"passed": 0, "failed": 0, "errored": 1, "tail": "1 error"},
    )
    assert out["verdict"] == "skipped"


def test_all_pass_proceeds(armed):
    out = sg.run_stress_gate(
        "id", "diff", "intent",
        generate=lambda d, i: "def test_x():\n    assert 1 == 1\n",
        run_tests=lambda code, aid: {"passed": 3, "failed": 0, "errored": 0, "tail": "3 passed"},
    )
    assert out["verdict"] == "pass"


def test_invalid_generated_code_skips(armed):
    out = sg.run_stress_gate(
        "id", "diff", "intent",
        generate=lambda d, i: "def test_x(:::broken syntax",
        run_tests=lambda code, aid: pytest.fail("should not run on invalid code"),
    )
    assert out["verdict"] == "skipped"


def test_generation_returning_none_skips(armed):
    out = sg.run_stress_gate(
        "id", "diff", "intent",
        generate=lambda d, i: None,
        run_tests=lambda code, aid: pytest.fail("should not run when no code"),
    )
    assert out["verdict"] == "skipped"


# --- pure-function units ---

def test_parse_pytest_counts():
    assert sg._parse_pytest("3 passed, 2 failed, 1 error in 0.1s") == {"passed": 3, "failed": 2, "errored": 1}


def test_decide_failed_beats_passed():
    assert sg._decide({"passed": 5, "failed": 1, "errored": 0})["verdict"] == "fail"


def test_decide_error_only_skips():
    assert sg._decide({"passed": 0, "failed": 0, "errored": 2})["verdict"] == "skipped"


def test_strip_fences():
    assert sg._strip_fences("```python\ndef test(): pass\n```") == "def test(): pass"


# --- differential reliability (the research-grounded core) ---

def test_differential_fail_on_change_pass_on_baseline_rejects(armed):
    # generated tests PASS on the unchanged baseline but FAIL on the change
    # → a genuine new edge-case regression → reject.
    out = sg.run_stress_gate(
        "id", "diff", "intent",
        generate=lambda d, i: "def test_x():\n    assert True\n",
        run_tests=lambda c, a: {
            "passed": 0, "failed": 1, "errored": 0, "tail": "1 failed",
            "baseline": {"passed": 1, "failed": 0, "errored": 0},
        },
    )
    assert out["verdict"] == "fail"


def test_differential_fail_on_baseline_too_skips_not_rejects(armed):
    # generated tests fail even on the UNCHANGED code → unreliable, NOT a
    # regression the change caused → skip, never a false-reject.
    out = sg.run_stress_gate(
        "id", "diff", "intent",
        generate=lambda d, i: "def test_x():\n    assert True\n",
        run_tests=lambda c, a: {
            "passed": 0, "failed": 1, "errored": 0, "tail": "1 failed",
            "baseline": {"passed": 0, "failed": 1, "errored": 0},
        },
    )
    assert out["verdict"] == "skipped"


def test_decide_differential_dirty_baseline_skips():
    assert sg._decide({"failed": 1}, {"failed": 1})["verdict"] == "skipped"


def test_decide_differential_clean_baseline_fails():
    assert sg._decide({"failed": 1, "passed": 0}, {"failed": 0, "errored": 0})["verdict"] == "fail"
