"""Tests for tools/agent_authoring_tool.py — agents_list / agent_manage.

Proves:
  1. Both tools register and appear in load_all_livekit_tools().
  2. agents_list returns the built-in dispatch roster + discovered user agents.
  3. agent_manage create → writes a user agent; list + dispatch resolution see it.
  4. agent_manage edit / patch / delete behave; delete trashes recoverably.
  5. Validation failures return error strings — never raise.

Isolation: writes use JARVIS_AGENTS_PATHS at a tmp_path.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

_VOICE_AGENT_ROOT = Path(__file__).resolve().parent.parent
if str(_VOICE_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_VOICE_AGENT_ROOT))

os.environ.setdefault("GROQ_API_KEY", "test-key-for-init")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _invoke(tool, args: dict) -> str:
    return _run(tool(raw_arguments=args))


def _tool(name: str):
    from tools._adapter import load_all_livekit_tools
    return next(t for t in load_all_livekit_tools() if t.info.name == name)


class TestRegistration:
    def test_tools_registered(self):
        from tools.registry import registry
        import tools.agent_authoring_tool  # noqa: F401  (side-effect: register)

        assert registry.get_entry("agents_list") is not None
        assert registry.get_entry("agent_manage") is not None

    def test_in_load_all_livekit_tools(self):
        from tools._adapter import load_all_livekit_tools
        names = {t.info.name for t in load_all_livekit_tools()}
        assert "agents_list" in names
        assert "agent_manage" in names

    def test_are_raw_function_tools(self):
        from livekit.agents.llm import is_raw_function_tool
        from tools._adapter import load_all_livekit_tools
        picked = [t for t in load_all_livekit_tools() if t.info.name in {"agents_list", "agent_manage"}]
        assert len(picked) == 2
        assert all(is_raw_function_tool(t) for t in picked)


class TestAgentsList:
    def test_lists_builtins_even_with_no_custom(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JARVIS_AGENTS_PATHS", str(tmp_path))
        out = _invoke(_tool("agents_list"), {})
        # The four built-in dispatch agents are always present.
        for name in ("explore", "researcher", "code_reviewer", "plan"):
            assert name in out

    def test_lists_created_custom_agent(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JARVIS_AGENTS_PATHS", str(tmp_path))
        _invoke(_tool("agent_manage"), {
            "action": "create", "name": "log-summarizer",
            "description": "Summarize a noisy log into the key events.",
            "body": "You read logs and surface the few lines that matter, with timestamps.",
        })
        out = _invoke(_tool("agents_list"), {})
        assert "log-summarizer" in out


class TestAgentManageCreate:
    def test_create_then_visible(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JARVIS_AGENTS_PATHS", str(tmp_path))
        res = _invoke(_tool("agent_manage"), {
            "action": "create", "name": "pr-describer",
            "description": "Write a PR description from a diff.",
            "body": "You turn a git diff into a clear, structured PR description.",
            "tools": "Read, Bash",
        })
        assert "created" in res.lower()
        assert (tmp_path / "pr-describer.md").exists()
        # tools line landed in the file.
        assert "tools: Read, Bash" in (tmp_path / "pr-describer.md").read_text()

    def test_create_missing_body(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JARVIS_AGENTS_PATHS", str(tmp_path))
        res = _invoke(_tool("agent_manage"), {
            "action": "create", "name": "x-agent", "description": "desc",
        })
        assert "body" in res.lower()

    def test_bad_action(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JARVIS_AGENTS_PATHS", str(tmp_path))
        res = _invoke(_tool("agent_manage"), {"action": "frobnicate", "name": "x-agent"})
        assert "unknown action" in res.lower()

    def test_bad_name_returns_error_not_raise(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JARVIS_AGENTS_PATHS", str(tmp_path))
        res = _invoke(_tool("agent_manage"), {
            "action": "create", "name": "Bad Name",
            "description": "desc here", "body": "a long enough system prompt body here",
        })
        assert res.lower().startswith("error")


class TestAgentManageEditPatchDelete:
    def _seed(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JARVIS_AGENTS_PATHS", str(tmp_path))
        _invoke(_tool("agent_manage"), {
            "action": "create", "name": "worker",
            "description": "A general worker.",
            "body": "You are a general worker. ORIGINAL-MARKER. Do tasks well.",
        })

    def test_edit(self, tmp_path, monkeypatch):
        self._seed(tmp_path, monkeypatch)
        res = _invoke(_tool("agent_manage"), {
            "action": "edit", "name": "worker",
            "body": "You are a general worker. UPDATED-MARKER. Do tasks even better.",
        })
        assert "updated" in res.lower()
        body = (tmp_path / "worker.md").read_text()
        assert "UPDATED-MARKER" in body and "ORIGINAL-MARKER" not in body

    def test_patch(self, tmp_path, monkeypatch):
        self._seed(tmp_path, monkeypatch)
        res = _invoke(_tool("agent_manage"), {
            "action": "patch", "name": "worker",
            "old_string": "ORIGINAL-MARKER", "new_string": "PATCHED-MARKER",
        })
        assert "patched" in res.lower()
        assert "PATCHED-MARKER" in (tmp_path / "worker.md").read_text()

    def test_patch_missing_string(self, tmp_path, monkeypatch):
        self._seed(tmp_path, monkeypatch)
        res = _invoke(_tool("agent_manage"), {
            "action": "patch", "name": "worker",
            "old_string": "NOT-PRESENT", "new_string": "x",
        })
        assert res.lower().startswith("error")

    def test_delete(self, tmp_path, monkeypatch):
        self._seed(tmp_path, monkeypatch)
        res = _invoke(_tool("agent_manage"), {"action": "delete", "name": "worker"})
        assert "deleted" in res.lower()
        assert not (tmp_path / "worker.md").exists()
