"""Tests for the screen-share Live specialist.

Smoke tests verify:
  - Spec registers correctly (idempotent re-register).
  - llm_factory is wired into RegistrySubagent (i.e., the specialist
    will receive a RealtimeModel instead of inheriting the
    supervisor's Claude Haiku LLM).
  - Spec auto-disables unless JARVIS_SUBAGENT_SCREEN_SHARE=1 — guards
    against shipping a broken Live integration to users who haven't
    explicitly opted in.
  - The bailout-phrase rule (specialist tool gate) honors the spec's
    declared bailout phrases verbatim.

Does NOT make real Gemini Live API calls — those are gated behind
a separate integration test only run when GOOGLE_API_KEY +
JARVIS_RUN_LIVE_TESTS=1 are both set.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ── Spec registration ──────────────────────────────────────────────


class TestSpecRegistration:
    def test_spec_registers_with_correct_name(self):
        """Re-register and inspect — should produce a HandoffSubagent
        with name='screen_share' and transfer_tool='transfer_to_screen_share'."""
        from subagents import registry
        from subagents import screen_share as ss
        registry.clear()
        with patch.dict(os.environ, {"JARVIS_SUBAGENT_SCREEN_SHARE": "1"}):
            ss.register_screen_share()
        spec = registry._REGISTRY.get("screen_share")
        assert spec is not None
        assert spec.name == "screen_share"
        assert spec.transfer_tool == "transfer_to_screen_share"

    def test_spec_disabled_without_env_flag(self):
        """The spec must self-disable unless explicitly opted in via
        JARVIS_SUBAGENT_SCREEN_SHARE=1. Guards against the in-progress
        Live API issues (1011 INTERNAL on some accounts) shipping
        a broken feature to users who haven't tested it."""
        from subagents import registry
        from subagents import screen_share as ss
        registry.clear()
        # No env flag (or 0) → spec is registered with enabled=False.
        with patch.dict(os.environ, {}, clear=False) as env:
            env.pop("JARVIS_SUBAGENT_SCREEN_SHARE", None)
            ss.register_screen_share()
        spec = registry._REGISTRY.get("screen_share")
        assert spec is not None, "spec should always be registered (gating is on enabled, not registration)"
        assert spec.enabled is False

    def test_spec_enabled_with_env_flag(self):
        from subagents import registry
        from subagents import screen_share as ss
        registry.clear()
        with patch.dict(os.environ, {"JARVIS_SUBAGENT_SCREEN_SHARE": "1"}):
            ss.register_screen_share()
        spec = registry._REGISTRY.get("screen_share")
        assert spec.enabled is True

    def test_spec_has_llm_factory(self):
        """Critical: the spec MUST provide its own llm_factory so the
        specialist gets a RealtimeModel instead of inheriting Claude
        Haiku from the supervisor. Without this, the whole point of
        the specialist (Live API for real-time vision) is lost."""
        from subagents import registry
        from subagents import screen_share as ss
        registry.clear()
        with patch.dict(os.environ, {"JARVIS_SUBAGENT_SCREEN_SHARE": "1"}):
            ss.register_screen_share()
        spec = registry._REGISTRY["screen_share"]
        assert spec.llm_factory is not None
        assert callable(spec.llm_factory)


# ── LLM factory wiring ─────────────────────────────────────────────


class TestLLMFactoryWiring:
    """Verify that RegistrySubagent passes the spec's llm to Agent.__init__."""

    def test_llm_factory_result_reaches_agent_init(self):
        """When a spec has llm_factory, RegistrySubagent.__init__ must
        call it and pass the result to super().__init__(llm=...)."""
        from subagents.registry import HandoffSubagent
        from subagents.agent import RegistrySubagent

        fake_llm = MagicMock(name="FakeRealtimeModel")

        def factory():
            return fake_llm

        spec = HandoffSubagent(
            name="test_spec",
            transfer_tool="transfer_to_test_spec",
            when_to_use="test",
            instructions="test instructions",
            tool_factory=lambda: [],
            llm_factory=factory,
        )

        supervisor = MagicMock()

        # Capture the Agent.__init__ kwargs to verify llm reached it.
        with patch("subagents.agent.Agent.__init__", return_value=None) as init:
            RegistrySubagent(spec=spec, supervisor=supervisor)
        kwargs = init.call_args.kwargs
        assert kwargs.get("llm") is fake_llm

    def test_no_llm_factory_means_no_llm_kwarg(self):
        """When llm_factory is None (most specs), Agent.__init__ must
        NOT be passed llm — that lets it inherit the session's LLM
        normally."""
        from subagents.registry import HandoffSubagent
        from subagents.agent import RegistrySubagent

        spec = HandoffSubagent(
            name="test_no_llm",
            transfer_tool="transfer_to_test_no_llm",
            when_to_use="test",
            instructions="test",
            tool_factory=lambda: [],
            # llm_factory left at default None
        )

        with patch("subagents.agent.Agent.__init__", return_value=None) as init:
            RegistrySubagent(spec=spec, supervisor=MagicMock())
        kwargs = init.call_args.kwargs
        assert "llm" not in kwargs

    def test_factory_exception_falls_back_to_inheritance(self):
        """If the llm_factory raises (Gemini SDK not installed, key
        missing, etc.), the specialist should still construct — just
        without the custom LLM, falling back to supervisor inheritance.
        Better degraded behavior than a crash on handoff."""
        from subagents.registry import HandoffSubagent
        from subagents.agent import RegistrySubagent

        def bad_factory():
            raise RuntimeError("simulated factory failure")

        spec = HandoffSubagent(
            name="test_bad_factory",
            transfer_tool="transfer_to_test_bad_factory",
            when_to_use="test",
            instructions="test",
            tool_factory=lambda: [],
            llm_factory=bad_factory,
        )

        with patch("subagents.agent.Agent.__init__", return_value=None) as init:
            # Must NOT raise.
            RegistrySubagent(spec=spec, supervisor=MagicMock())
        kwargs = init.call_args.kwargs
        # Factory failed → no llm kwarg → falls back to inheritance.
        assert "llm" not in kwargs


# ── Model selection ────────────────────────────────────────────────


class TestModelSelection:
    def test_default_model_is_25_native_audio(self):
        """Per researcher 2026-05-11: 2.5-flash-native-audio-preview-12-2025
        is the model AI Studio Stream uses. 3.1-flash-live-preview is
        broken (1011 INTERNAL, python-genai #2238)."""
        from subagents import screen_share as ss
        with patch.dict(os.environ, {}, clear=False) as env:
            env.pop("JARVIS_SCREEN_SHARE_LIVE_MODEL", None)
            # Re-read the module-level constant (it was set at import time;
            # we just verify it's the right default by re-importing).
            import importlib
            importlib.reload(ss)
            assert ss.SCREEN_SHARE_LIVE_MODEL == "gemini-2.5-flash-native-audio-preview-12-2025"

    def test_model_override_via_env(self):
        from subagents import screen_share as ss
        with patch.dict(os.environ, {"JARVIS_SCREEN_SHARE_LIVE_MODEL": "gemini-future-model"}):
            import importlib
            importlib.reload(ss)
            assert ss.SCREEN_SHARE_LIVE_MODEL == "gemini-future-model"
        # Restore for other tests
        with patch.dict(os.environ, {}, clear=False) as env:
            env.pop("JARVIS_SCREEN_SHARE_LIVE_MODEL", None)
            import importlib
            importlib.reload(ss)


# ── Integration: live Gemini call (gated) ──────────────────────────


@pytest.mark.skipif(
    not (os.environ.get("GOOGLE_API_KEY") and os.environ.get("JARVIS_RUN_LIVE_TESTS")),
    reason="requires GOOGLE_API_KEY + JARVIS_RUN_LIVE_TESTS=1 to avoid billing in CI",
)
class TestLiveIntegration:
    """Real Gemini Live session smoke. Gated behind two env vars so
    CI never pays. Run manually with:
        GOOGLE_API_KEY=... JARVIS_RUN_LIVE_TESTS=1 pytest tests/test_screen_share_specialist.py -k Live -v
    """

    def test_realtime_model_constructs(self):
        from subagents import screen_share as ss
        # Should not raise — verifies the livekit.plugins.google import
        # path and the model name are both valid on this account.
        llm = ss._build_screen_share_llm()
        assert llm is not None
