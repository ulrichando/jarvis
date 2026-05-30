"""Tests for evolution/fitness.py — normalization, guardrails, composite, is_fitter.

Builds WindowSignals fixtures directly (no DB) and asserts the constitution:
WEIGHTS sum to 1.0, lexicographic guardrail veto, interruption is never a
guardrail, empty windows never pass, and the env-tunable ttfw target.
"""
from __future__ import annotations

from evolution.fitness import WEIGHTS, GUARDRAILS, score, is_fitter
from evolution.signals import WindowSignals, compute_signals


def _sig(*, n_turns=10, n_checked=0, reask_rate=0.0, confab_quality=1.0,
         median_ttfw_ms=0.0, clean_action_rate=1.0, interruption_rate=0.0):
    return WindowSignals(n_turns, n_checked, reask_rate, confab_quality,
                         median_ttfw_ms, clean_action_rate, interruption_rate)


def test_weights_sum_to_one():
    assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9


def test_perfect_window_scores_high():
    r = score(_sig(reask_rate=0.0, confab_quality=1.0, median_ttfw_ms=0.0,
                   clean_action_rate=1.0, interruption_rate=0.0, n_turns=10))
    assert r.composite >= 0.99
    assert r.passed is True


def test_reask_guardrail_veto():
    bad = score(_sig(reask_rate=0.5, n_turns=10))   # axis 0.5 < 0.70 floor
    assert bad.passed is False
    good = score(_sig(reask_rate=0.0, confab_quality=1.0, n_turns=10))
    assert is_fitter(bad, good) is False


def test_confab_guardrail_veto():
    r = score(_sig(confab_quality=0.5, n_turns=10))  # axis 0.5 < 0.70 floor
    assert r.passed is False


def test_interruption_never_vetoes():
    r = score(_sig(reask_rate=0.0, confab_quality=1.0, interruption_rate=1.0,
                   n_turns=10))
    assert r.passed is True
    assert "interruption" not in GUARDRAILS


def test_empty_window_never_passes():
    r = score(compute_signals([]))
    assert r.passed is False


def test_latency_uses_env_target(monkeypatch):
    monkeypatch.setenv("JARVIS_TTFW_TARGET_MS", "2000")
    r = score(_sig(median_ttfw_ms=2000.0, n_turns=10))
    assert r.per_axis["latency"] == 1.0


def test_is_fitter_requires_guardrails_and_delta():
    incumbent = score(_sig(reask_rate=0.10, confab_quality=0.90, n_turns=10))
    # Guardrail-failing but higher composite → NOT fitter.
    fail_high = score(_sig(reask_rate=0.5, confab_quality=1.0,
                           median_ttfw_ms=0.0, clean_action_rate=1.0,
                           interruption_rate=0.0, n_turns=10))
    assert fail_high.passed is False
    assert is_fitter(fail_high, incumbent) is False
    # Guardrail-passing higher composite → IS fitter.
    pass_high = score(_sig(reask_rate=0.0, confab_quality=1.0,
                           median_ttfw_ms=0.0, clean_action_rate=1.0,
                           interruption_rate=0.0, n_turns=10))
    assert pass_high.passed is True
    assert pass_high.composite > incumbent.composite
    assert is_fitter(pass_high, incumbent) is True
