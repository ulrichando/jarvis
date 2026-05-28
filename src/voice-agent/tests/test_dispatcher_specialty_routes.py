"""Integration tests for build_dispatching_llm + specialty_routes wiring.

After Task 4, the dispatcher's route map exposes 8 routes (BANTER +
5 TASK_* + REASONING + EMOTIONAL). The legacy JARVIS_TASK_MODEL env
still applies to all TASK_* sub-routes."""
from __future__ import annotations

import os
import sys
import unittest.mock as mock
from pathlib import Path

import pytest

# Tests run from the voice-agent root.
sys.path.insert(0, str(Path(__file__).parent.parent))

# Groq + Anthropic + DeepSeek plugins read API keys at construction
# time even when the request never goes out — set placeholders so the
# rungs build cleanly. Same pattern as test_llm_dispatcher_build.
os.environ.setdefault("GROQ_API_KEY", "test-key-for-init")
os.environ.setdefault("DEEPSEEK_API_KEY", "test-deepseek-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")


def _route_keys(disp) -> set[str]:
    """Inspect the dispatcher to extract its route map keys.

    DispatchingLLM (pipeline/dispatching_llm.py) stores its per-route
    map on the `inners` attribute."""
    for attr in ("inners", "route_to_llm", "_route_map", "llms", "_llms", "routes"):
        val = getattr(disp, attr, None)
        if isinstance(val, dict):
            return set(val.keys())
    raise AssertionError(
        f"Could not find a dict route map on DispatchingLLM "
        f"(checked attrs: inners, route_to_llm, _route_map, llms, _llms, routes). "
        f"Inspect providers/llm.py::DispatchingLLM to find the real name."
    )


def test_dispatcher_exposes_all_8_routes():
    """The 4→8 route expansion lands in the dispatcher."""
    from providers.llm import build_dispatching_llm
    disp = build_dispatching_llm()
    routes = _route_keys(disp)
    expected = {
        "BANTER",
        "TASK_DESKTOP", "TASK_BROWSER", "TASK_CODE", "TASK_FILES", "TASK_OTHER",
        "REASONING", "EMOTIONAL",
    }
    missing = expected - routes
    assert not missing, f"Routes missing from dispatcher: {missing}"


def test_legacy_jarvis_task_model_still_consulted(monkeypatch):
    """JARVIS_TASK_MODEL still applies to all TASK_* sub-routes when set."""
    from pipeline import specialty_routes  # noqa: F401 — sanity import
    # Per-sub-route env vars must be UNSET for the legacy fallback to apply.
    for env_name in ("JARVIS_TASK_DESKTOP_MODEL", "JARVIS_TASK_BROWSER_MODEL",
                     "JARVIS_TASK_CODE_MODEL", "JARVIS_TASK_FILES_MODEL",
                     "JARVIS_TASK_OTHER_MODEL"):
        monkeypatch.delenv(env_name, raising=False)
    monkeypatch.setenv("JARVIS_TASK_MODEL", "claude-opus-4-7")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")

    from providers.llm import build_dispatching_llm
    disp = build_dispatching_llm()

    # Each TASK_* sub-route should land on the legacy Opus model.
    for route in ("TASK_DESKTOP", "TASK_BROWSER", "TASK_FILES", "TASK_OTHER"):
        inner = disp.inners[route]
        label = getattr(inner, "_jarvis_label", "")
        assert label == "anthropic:claude-opus-4-7", (
            f"route {route} expected anthropic:claude-opus-4-7 from legacy "
            f"JARVIS_TASK_MODEL; got {label!r}"
        )
    # TASK_CODE also gets the legacy Opus model when JARVIS_TASK_MODEL is set
    # (legacy override applies to all TASK_* sub-routes uniformly).
    inner_code = disp.inners["TASK_CODE"]
    label_code = getattr(inner_code, "_jarvis_label", "")
    assert label_code == "anthropic:claude-opus-4-7", (
        f"TASK_CODE expected anthropic:claude-opus-4-7 from legacy "
        f"JARVIS_TASK_MODEL; got {label_code!r}"
    )


def test_per_sub_route_env_wins_over_legacy(monkeypatch):
    """JARVIS_TASK_DESKTOP_MODEL beats JARVIS_TASK_MODEL for TASK_DESKTOP."""
    from pipeline.specialty_routes import get_primary_model
    monkeypatch.setenv("JARVIS_TASK_MODEL", "claude-opus-4-7")
    monkeypatch.setenv("JARVIS_TASK_DESKTOP_MODEL", "claude-haiku-4-5")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")
    # specialty_routes.get_primary_model honors per-sub-route env first.
    assert get_primary_model("TASK_DESKTOP") == "claude-haiku-4-5"

    # Confirm the dispatcher also picks the per-sub-route value.
    from providers.llm import build_dispatching_llm
    disp = build_dispatching_llm()
    inner = disp.inners["TASK_DESKTOP"]
    label = getattr(inner, "_jarvis_label", "")
    assert label == "anthropic:claude-haiku-4-5", (
        f"TASK_DESKTOP per-sub-route env should beat legacy; got {label!r}"
    )
    # TASK_BROWSER (no per-sub-route env set) still picks up the legacy.
    inner_browser = disp.inners["TASK_BROWSER"]
    label_browser = getattr(inner_browser, "_jarvis_label", "")
    assert label_browser == "anthropic:claude-opus-4-7", (
        f"TASK_BROWSER expected legacy claude-opus-4-7; got {label_browser!r}"
    )


def test_default_routes_use_specialty_defaults(monkeypatch):
    """With no env vars set, primaries come from specialty_routes defaults."""
    from pipeline.specialty_routes import get_primary_model
    for env_name in (
        "JARVIS_TASK_MODEL",
        "JARVIS_TASK_DESKTOP_MODEL", "JARVIS_TASK_BROWSER_MODEL",
        "JARVIS_TASK_CODE_MODEL",    "JARVIS_TASK_FILES_MODEL",
        "JARVIS_TASK_OTHER_MODEL",
        "JARVIS_BANTER_MODEL", "JARVIS_REASONING_MODEL", "JARVIS_EMOTIONAL_MODEL",
    ):
        monkeypatch.delenv(env_name, raising=False)
    assert get_primary_model("TASK_DESKTOP") == "claude-sonnet-4-6"
    assert get_primary_model("TASK_CODE")    == "deepseek-v4-flash"
    assert get_primary_model("BANTER")       == "claude-haiku-4-5"
    assert get_primary_model("REASONING")    == "claude-sonnet-4-6"


def test_dispatcher_labels_match_specialty_defaults(monkeypatch):
    """End-to-end: no env vars → dispatcher's per-route labels match
    the specialty_routes defaults."""
    for env_name in (
        "JARVIS_TASK_MODEL",
        "JARVIS_TASK_DESKTOP_MODEL", "JARVIS_TASK_BROWSER_MODEL",
        "JARVIS_TASK_CODE_MODEL",    "JARVIS_TASK_FILES_MODEL",
        "JARVIS_TASK_OTHER_MODEL",
        "JARVIS_BANTER_MODEL", "JARVIS_REASONING_MODEL", "JARVIS_EMOTIONAL_MODEL",
    ):
        monkeypatch.delenv(env_name, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-deepseek-key")

    from providers.llm import build_dispatching_llm
    disp = build_dispatching_llm()

    # TASK_DESKTOP / TASK_BROWSER → claude-sonnet-4-6
    for route in ("TASK_DESKTOP", "TASK_BROWSER"):
        inner = disp.inners[route]
        label = getattr(inner, "_jarvis_label", "")
        assert label == "anthropic:claude-sonnet-4-6", (
            f"{route} expected anthropic:claude-sonnet-4-6; got {label!r}"
        )
    # TASK_CODE → deepseek-v4-flash (routed through DeepSeek builder).
    inner_code = disp.inners["TASK_CODE"]
    label_code = getattr(inner_code, "_jarvis_label", "")
    assert label_code == "deepseek:deepseek-v4-flash", (
        f"TASK_CODE expected deepseek:deepseek-v4-flash; got {label_code!r}"
    )
    # TASK_FILES → claude-haiku-4-5 (file ops are explicit; Haiku is sufficient)
    inner_files = disp.inners["TASK_FILES"]
    label_files = getattr(inner_files, "_jarvis_label", "")
    assert label_files == "anthropic:claude-haiku-4-5", (
        f"TASK_FILES expected anthropic:claude-haiku-4-5; got {label_files!r}"
    )
    # TASK_OTHER → claude-sonnet-4-6 (Sonnet for orchestration reliability —
    # see specialty_routes.py)
    inner_other = disp.inners["TASK_OTHER"]
    label_other = getattr(inner_other, "_jarvis_label", "")
    assert label_other == "anthropic:claude-sonnet-4-6", (
        f"TASK_OTHER expected anthropic:claude-sonnet-4-6; got {label_other!r}"
    )
    # BANTER / EMOTIONAL → claude-haiku-4-5; REASONING → claude-sonnet-4-6
    assert getattr(disp.inners["BANTER"], "_jarvis_label", "") == "anthropic:claude-haiku-4-5"
    assert getattr(disp.inners["EMOTIONAL"], "_jarvis_label", "") == "anthropic:claude-haiku-4-5"
    assert getattr(disp.inners["REASONING"], "_jarvis_label", "") == "anthropic:claude-sonnet-4-6"


def test_legacy_task_route_still_present():
    """The legacy 'TASK' route is kept for backwards-compat with callers
    that have not migrated to the 5-way TASK_* split."""
    from providers.llm import build_dispatching_llm
    disp = build_dispatching_llm()
    assert "TASK" in disp.inners


def test_task_override_propagates_across_task_subroutes(monkeypatch):
    """When a tray-pinned LLM is passed via task_override, ALL TASK_*
    sub-routes adopt it (not just the legacy TASK key)."""
    from unittest.mock import MagicMock
    for env_name in (
        "JARVIS_TASK_MODEL",
        "JARVIS_TASK_DESKTOP_MODEL", "JARVIS_TASK_BROWSER_MODEL",
        "JARVIS_TASK_CODE_MODEL",    "JARVIS_TASK_FILES_MODEL",
        "JARVIS_TASK_OTHER_MODEL",
    ):
        monkeypatch.delenv(env_name, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")

    pinned = MagicMock(spec=["_jarvis_label"])
    pinned._jarvis_label = "tray-pinned:gpt-5-mini"

    from providers.llm import build_dispatching_llm
    disp = build_dispatching_llm(task_override=pinned)

    for route in ("TASK", "TASK_DESKTOP", "TASK_BROWSER", "TASK_CODE", "TASK_FILES", "TASK_OTHER"):
        inner = disp.inners[route]
        assert inner is pinned, (
            f"route {route} expected tray-pinned override; got {type(inner).__name__}"
        )
    # Non-TASK routes keep their per-route defaults (not the pin).
    for route in ("BANTER", "REASONING", "EMOTIONAL"):
        inner = disp.inners[route]
        assert inner is not pinned, f"route {route} unexpectedly received the TASK pin"


def test_action_routes_force_tool_choice(monkeypatch):
    """Action routes (TASK_DESKTOP/BROWSER/CODE/FILES) must construct their
    primary LLM with tool_choice="required" so the model can't reply with
    narration-only on a turn that needs an action. Non-action routes
    (BANTER, EMOTIONAL, REASONING, TASK_OTHER) must NOT force tool_choice."""
    for env_name in (
        "JARVIS_TASK_MODEL",
        "JARVIS_TASK_DESKTOP_MODEL", "JARVIS_TASK_BROWSER_MODEL",
        "JARVIS_TASK_CODE_MODEL",    "JARVIS_TASK_FILES_MODEL",
        "JARVIS_TASK_OTHER_MODEL",
        "JARVIS_BANTER_MODEL", "JARVIS_REASONING_MODEL", "JARVIS_EMOTIONAL_MODEL",
    ):
        monkeypatch.delenv(env_name, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-deepseek-key")

    from providers.llm import build_dispatching_llm
    disp = build_dispatching_llm()

    def primary_opts(route: str):
        """Walk through a FallbackAdapter to the primary inner LLM and
        return its tool_choice setting (None if not set)."""
        outer = disp.inners[route]
        # FallbackAdapter exposes its rung list as ._llm_instances (verified
        # against livekit/agents/llm/fallback_adapter.py:70 — the constructor
        # sets `self._llm_instances = llm`). Primary is rung 0. The unwrapped
        # primary (single-rung case) IS the inner already.
        primary = outer
        for attr in ("_llm_instances", "_llms", "llms"):
            chain = getattr(outer, attr, None)
            if chain:
                primary = chain[0]
                break
        # tool_choice ends up on primary._opts.tool_choice for Anthropic /
        # OpenAI / DeepSeek alike.
        opts = getattr(primary, "_opts", None)
        if opts is None:
            return None
        tc = getattr(opts, "tool_choice", None)
        # NOT_GIVEN sentinel reads as not-required.
        try:
            from livekit.agents.types import NOT_GIVEN
            if tc is NOT_GIVEN:
                return None
        except Exception:
            pass
        return tc

    # Forced (action routes):
    for route in ("TASK_DESKTOP", "TASK_BROWSER", "TASK_CODE", "TASK_FILES"):
        tc = primary_opts(route)
        assert tc == "required", (
            f"{route} expected tool_choice='required' on primary LLM; got {tc!r}"
        )

    # Not forced (conversational / broad-task routes):
    for route in ("BANTER", "EMOTIONAL", "REASONING", "TASK_OTHER"):
        tc = primary_opts(route)
        assert tc != "required", (
            f"{route} must NOT force tool_choice; got {tc!r}"
        )
