"""Pin the canonical subagent env-var names.

The 2026-05-11 evening rename swept "specialist" → "subagent"
across the codebase and dropped the legacy JARVIS_SPECIALIST_*
fallback aliases. These tests pin the new contract so a future
rename can't quietly regress it.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestRetryCeiling:
    def test_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("JARVIS_SUBAGENT_NO_TOOL_RETRY_CEILING", raising=False)
        monkeypatch.delenv("JARVIS_SPECIALIST_NO_TOOL_RETRY_CEILING", raising=False)
        from subagents.agent import _no_tool_retry_ceiling
        assert _no_tool_retry_ceiling() == 3

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("JARVIS_SUBAGENT_NO_TOOL_RETRY_CEILING", "5")
        from subagents.agent import _no_tool_retry_ceiling
        assert _no_tool_retry_ceiling() == 5

    def test_legacy_name_NOT_honored(self, monkeypatch):
        """JARVIS_SPECIALIST_NO_TOOL_RETRY_CEILING was the pre-2026-05-11
        name. After the rename it has no effect — old systemd unit
        files must be updated. This test pins that contract."""
        monkeypatch.delenv("JARVIS_SUBAGENT_NO_TOOL_RETRY_CEILING", raising=False)
        monkeypatch.setenv("JARVIS_SPECIALIST_NO_TOOL_RETRY_CEILING", "9")
        from subagents.agent import _no_tool_retry_ceiling
        assert _no_tool_retry_ceiling() == 3  # default, NOT 9


class TestConfigExports:
    """pipeline.config exports the canonical names only; the legacy
    SPECIALIST_* aliases were dropped in the terminology sweep."""

    def test_canonical_names_exist(self):
        from pipeline import config
        assert hasattr(config, "SUBAGENT_TOOL_GATE")
        assert hasattr(config, "SUBAGENT_NO_TOOL_RETRY_CEILING")

    def test_legacy_names_removed(self):
        from pipeline import config
        # The pre-rename SPECIALIST_TOOL_GATE / SPECIALIST_NO_TOOL_
        # RETRY_CEILING aliases are gone. Any consumer still doing
        # `from pipeline.config import SPECIALIST_TOOL_GATE` will get
        # an ImportError — and that's the point. Sweep made the old
        # term invalid everywhere.
        assert not hasattr(config, "SPECIALIST_TOOL_GATE")
        assert not hasattr(config, "SPECIALIST_NO_TOOL_RETRY_CEILING")
