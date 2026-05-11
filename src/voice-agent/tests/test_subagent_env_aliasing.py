"""Verify subagents/agent.py env-var aliasing — new
JARVIS_SUBAGENT_* names win over legacy JARVIS_SPECIALIST_*,
legacy still honored with a one-time deprecation warning.

Added 2026-05-11 evening when the specialist→subagent terminology
rename landed. The old env names need to keep working so user-side
systemd unit files don't silently break.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture(autouse=True)
def _reset_warned_set():
    """Each test gets a clean 'warned' tracker so multiple legacy
    reads in one test session don't suppress the warning."""
    from subagents import agent as ag
    ag._LEGACY_ENV_WARNED.clear()
    yield
    ag._LEGACY_ENV_WARNED.clear()


class TestRetryCeilingAliasing:
    def test_default_when_no_env_set(self, monkeypatch):
        monkeypatch.delenv("JARVIS_SUBAGENT_NO_TOOL_RETRY_CEILING", raising=False)
        monkeypatch.delenv("JARVIS_SPECIALIST_NO_TOOL_RETRY_CEILING", raising=False)
        from subagents.agent import _no_tool_retry_ceiling
        assert _no_tool_retry_ceiling() == 3

    def test_new_name_wins(self, monkeypatch):
        monkeypatch.setenv("JARVIS_SUBAGENT_NO_TOOL_RETRY_CEILING", "5")
        monkeypatch.setenv("JARVIS_SPECIALIST_NO_TOOL_RETRY_CEILING", "9")
        from subagents.agent import _no_tool_retry_ceiling
        assert _no_tool_retry_ceiling() == 5

    def test_legacy_name_honored(self, monkeypatch):
        monkeypatch.delenv("JARVIS_SUBAGENT_NO_TOOL_RETRY_CEILING", raising=False)
        monkeypatch.setenv("JARVIS_SPECIALIST_NO_TOOL_RETRY_CEILING", "7")
        from subagents.agent import _no_tool_retry_ceiling
        assert _no_tool_retry_ceiling() == 7

    def test_legacy_warns_once(self, monkeypatch, caplog):
        import logging
        monkeypatch.delenv("JARVIS_SUBAGENT_NO_TOOL_RETRY_CEILING", raising=False)
        monkeypatch.setenv("JARVIS_SPECIALIST_NO_TOOL_RETRY_CEILING", "4")
        from subagents.agent import _no_tool_retry_ceiling
        # Caplog captures logging at WARNING+ — match the message.
        with caplog.at_level(logging.WARNING, logger="jarvis.subagent"):
            _no_tool_retry_ceiling()
            _no_tool_retry_ceiling()  # second call: should NOT re-warn
        warn_lines = [r for r in caplog.records if "deprecated" in r.message]
        assert len(warn_lines) == 1, (
            f"expected exactly 1 deprecation warning, got {len(warn_lines)}: "
            f"{[r.message for r in warn_lines]}"
        )


class TestToolGateAliasing:
    """The tool-gate env reads at every task_done call. New name wins;
    legacy fallback works."""

    def test_gate_default_is_enabled(self, monkeypatch):
        monkeypatch.delenv("JARVIS_SUBAGENT_TOOL_GATE", raising=False)
        monkeypatch.delenv("JARVIS_SPECIALIST_TOOL_GATE", raising=False)
        from subagents.agent import _env_str_with_legacy
        assert _env_str_with_legacy(
            "JARVIS_SUBAGENT_TOOL_GATE",
            "JARVIS_SPECIALIST_TOOL_GATE",
            "1",
        ) == "1"

    def test_legacy_disable_still_works(self, monkeypatch):
        monkeypatch.delenv("JARVIS_SUBAGENT_TOOL_GATE", raising=False)
        monkeypatch.setenv("JARVIS_SPECIALIST_TOOL_GATE", "0")
        from subagents.agent import _env_str_with_legacy
        assert _env_str_with_legacy(
            "JARVIS_SUBAGENT_TOOL_GATE",
            "JARVIS_SPECIALIST_TOOL_GATE",
            "1",
        ) == "0"

    def test_new_name_overrides_legacy(self, monkeypatch):
        monkeypatch.setenv("JARVIS_SUBAGENT_TOOL_GATE", "0")
        monkeypatch.setenv("JARVIS_SPECIALIST_TOOL_GATE", "1")
        from subagents.agent import _env_str_with_legacy
        assert _env_str_with_legacy(
            "JARVIS_SUBAGENT_TOOL_GATE",
            "JARVIS_SPECIALIST_TOOL_GATE",
            "1",
        ) == "0"


class TestConfigExports:
    """pipeline.config still exports both the new and legacy constant
    names so old `from pipeline.config import SPECIALIST_TOOL_GATE`
    callers keep working."""

    def test_both_names_available(self):
        from pipeline import config
        assert hasattr(config, "SUBAGENT_TOOL_GATE")
        assert hasattr(config, "SUBAGENT_NO_TOOL_RETRY_CEILING")
        assert hasattr(config, "SPECIALIST_TOOL_GATE")
        assert hasattr(config, "SPECIALIST_NO_TOOL_RETRY_CEILING")

    def test_aliases_are_same_value(self):
        from pipeline import config
        assert config.SUBAGENT_TOOL_GATE == config.SPECIALIST_TOOL_GATE
        assert (
            config.SUBAGENT_NO_TOOL_RETRY_CEILING
            == config.SPECIALIST_NO_TOOL_RETRY_CEILING
        )
