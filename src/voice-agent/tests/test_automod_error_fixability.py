"""Tests for the fixability heuristic — caller emits intent only if
score >= 0.5. Spec 2026-05-27 Part 2."""
from __future__ import annotations

import os
import unittest.mock as mock

import pytest


def test_value_error_with_normal_message_is_fixable():
    from pipeline.automod.error_logger import _fixability_score
    frames = [("src/voice-agent/jarvis_agent.py", "foo")]
    score = _fixability_score("ValueError", "got 3, expected 2", frames)
    assert score >= 0.5


def test_auth_error_message_drops_below_floor():
    from pipeline.automod.error_logger import _fixability_score
    frames = [("src/voice-agent/jarvis_agent.py", "foo")]
    score = _fixability_score("RuntimeError",
                              "Anthropic returned 401: invalid api_key",
                              frames)
    assert score < 0.5, f"auth error should not be fixable, got {score}"


def test_provider_frame_drops_score():
    """Top jarvis frame in providers/ → typically transient/external."""
    from pipeline.automod.error_logger import _fixability_score
    frames = [("src/voice-agent/providers/llm.py", "build_dispatching_llm")]
    score = _fixability_score("ConnectionError", "connect: 502", frames)
    assert score < 0.5


def test_resilience_frame_drops_score():
    from pipeline.automod.error_logger import _fixability_score
    frames = [("src/voice-agent/resilience/circuit_breaker.py", "trip")]
    score = _fixability_score("RuntimeError", "breaker open", frames)
    assert score < 0.5


def test_high_fixability_class_with_no_low_signals_scores_well():
    """ValidationError + no auth/rate-limit hints + frame not in
    providers/ → comfortably above floor."""
    from pipeline.automod.error_logger import _fixability_score
    frames = [("src/voice-agent/tools/dispatch_agent.py", "handle_dispatch_agent")]
    score = _fixability_score("ValidationError", "field foo missing", frames)
    assert score >= 0.7  # 0.5 baseline + 0.3 for high-fixability class


def test_ignore_set_default_membership():
    """The default ignore set includes lifecycle exceptions we never fix."""
    from pipeline.automod.error_logger import _ignore_set
    s = _ignore_set()
    assert "CancelledError" in s
    assert "KeyboardInterrupt" in s
    assert "SystemExit" in s


def test_ignore_set_env_override_extends_default():
    """JARVIS_AUTOMOD_ERROR_IGNORE_EXC adds to (not replaces) the default."""
    from pipeline.automod.error_logger import _ignore_set
    with mock.patch.dict(os.environ,
                         {"JARVIS_AUTOMOD_ERROR_IGNORE_EXC": "MyCustomExc,AnotherExc"}):
        s = _ignore_set()
    assert "MyCustomExc" in s
    assert "AnotherExc" in s
    assert "CancelledError" in s  # default still present
