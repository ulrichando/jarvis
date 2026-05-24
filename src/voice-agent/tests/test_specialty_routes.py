"""Tests for pipeline.specialty_routes — model dispatch table + lookups."""
from __future__ import annotations

import os
import unittest.mock as mock

import pytest

from pipeline.specialty_routes import (
    TIER_PRIMARY, TIER_RETRY, TIER_ESCALATE, TIER_CROSS_PROVIDER,
    get_primary_model,
    get_route_ladder,
    routes_with_retry_chain,
    _DEFAULTS,
)


def test_all_8_routes_have_a_primary():
    for r in (
        "BANTER",
        "TASK_DESKTOP", "TASK_BROWSER", "TASK_CODE", "TASK_FILES", "TASK_OTHER",
        "REASONING", "EMOTIONAL",
    ):
        assert get_primary_model(r) is not None, f"{r} missing primary"


def test_task_desktop_primary_is_sonnet():
    # Clear any env override that may be set in dev.
    with mock.patch.dict(os.environ, {}, clear=False):
        os.environ.pop("JARVIS_TASK_DESKTOP_MODEL", None)
        assert get_primary_model("TASK_DESKTOP") == "claude-sonnet-4-6"


def test_task_code_primary_is_deepseek():
    with mock.patch.dict(os.environ, {}, clear=False):
        os.environ.pop("JARVIS_TASK_CODE_MODEL", None)
        assert get_primary_model("TASK_CODE") == "deepseek-v4-flash"


def test_task_files_primary_is_haiku():
    with mock.patch.dict(os.environ, {}, clear=False):
        os.environ.pop("JARVIS_TASK_FILES_MODEL", None)
        assert get_primary_model("TASK_FILES") == "claude-haiku-4-5"


def test_env_override_swaps_primary():
    with mock.patch.dict(os.environ, {"JARVIS_TASK_DESKTOP_MODEL": "claude-opus-4-7"}):
        assert get_primary_model("TASK_DESKTOP") == "claude-opus-4-7"


def test_env_override_blank_string_falls_back_to_default():
    with mock.patch.dict(os.environ, {"JARVIS_TASK_DESKTOP_MODEL": "   "}):
        assert get_primary_model("TASK_DESKTOP") == "claude-sonnet-4-6"


def test_get_ladder_returns_four_tiers():
    with mock.patch.dict(os.environ, {}, clear=False):
        os.environ.pop("JARVIS_TASK_DESKTOP_MODEL", None)
        os.environ.pop("JARVIS_KIMI_VOICE_EXPERIMENTAL", None)
        ladder = get_route_ladder("TASK_DESKTOP")
        assert len(ladder) == 4
        # primary, retry, escalate, cross_provider
        assert ladder[0] == "claude-sonnet-4-6"
        assert ladder[1] == "claude-sonnet-4-6"  # retry slot tracks primary
        assert ladder[2] == "claude-opus-4-7"
        assert ladder[3] == "gpt-5.1"


def test_banter_ladder_only_primary():
    ladder = get_route_ladder("BANTER")
    assert ladder[0] == "claude-haiku-4-5"
    assert ladder[1] is None  # no retry — gate bypasses BANTER
    assert ladder[2] is None
    assert ladder[3] is None


def test_emotional_ladder_only_primary():
    ladder = get_route_ladder("EMOTIONAL")
    assert ladder[0] == "claude-haiku-4-5"
    assert ladder[1] is None


def test_reasoning_cross_provider_is_gemini():
    ladder = get_route_ladder("REASONING")
    assert ladder[3] == "gemini-2.5-pro"


def test_task_other_cross_provider_is_gpt5_mini():
    ladder = get_route_ladder("TASK_OTHER")
    assert ladder[3] == "gpt-5-mini"


def test_kimi_suppressed_without_experimental_flag():
    with mock.patch.dict(os.environ, {}, clear=False):
        os.environ.pop("JARVIS_KIMI_VOICE_EXPERIMENTAL", None)
        ladder = get_route_ladder("TASK_BROWSER")
        # tier 2 (escalate) is Opus, NOT Kimi, when flag is off
        assert ladder[2] == "claude-opus-4-7"


def test_kimi_activates_with_experimental_flag():
    with mock.patch.dict(os.environ, {"JARVIS_KIMI_VOICE_EXPERIMENTAL": "1"}):
        ladder = get_route_ladder("TASK_BROWSER")
        assert ladder[2] == "kimi-k2.6-agent"


def test_env_override_propagates_to_retry_slot():
    """Retry tier always tracks the primary — env override flows through."""
    with mock.patch.dict(os.environ, {"JARVIS_TASK_CODE_MODEL": "claude-haiku-4-5"}):
        ladder = get_route_ladder("TASK_CODE")
        assert ladder[0] == "claude-haiku-4-5"
        assert ladder[1] == "claude-haiku-4-5"


def test_routes_with_retry_chain_excludes_banter_emotional():
    routes = routes_with_retry_chain()
    assert "BANTER" not in routes
    assert "EMOTIONAL" not in routes
    assert "TASK_DESKTOP" in routes
    assert "REASONING" in routes


def test_unknown_route_returns_empty_ladder():
    ladder = get_route_ladder("BOGUS_ROUTE")
    assert ladder == [None, None, None, None]


def test_unknown_route_primary_is_none():
    assert get_primary_model("BOGUS") is None
