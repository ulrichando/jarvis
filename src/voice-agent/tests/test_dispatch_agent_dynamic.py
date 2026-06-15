"""dispatch_agent ↔ user-authored agent integration.

Proves the additive dynamic-resolution path:
  * built-ins still resolve from _POLICY (and win on a name collision);
  * a user agent written under JARVIS_AGENTS_PATHS becomes dispatchable —
    _resolve_policy synthesizes a policy, _build_argv targets `--agent <name>`,
    get_ack_phrase yields a phrase, and the full foreground handler accepts it
    (subprocess mocked, no real bin/jarvis run);
  * an unknown name resolves to None and the handler reports a helpful error
    that includes the discovered custom agents.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ.setdefault("GROQ_API_KEY", "test-key-for-init")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")


def _make_fake_proc(stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0):
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.kill = MagicMock()
    proc.wait = AsyncMock(return_value=returncode)
    return proc


def _write_agent(root: Path, name: str, description: str = "A custom test agent.") -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{name}.md").write_text(
        f'---\nname: {name}\ndescription: "{description}"\n---\n\n'
        f"You are {name}. Do the specialized work described above.\n",
        encoding="utf-8",
    )


# ── resolution ─────────────────────────────────────────────────────────────


class TestResolvePolicy:
    def test_builtin_resolves_without_disk(self, monkeypatch):
        monkeypatch.setenv("JARVIS_AGENTS_PATHS", "/tmp/__no_such_agents_dir__")
        from tools.dispatch_agent import _resolve_policy
        pol = _resolve_policy("explore")
        assert pol is not None and pol["cli_agent"] == "Explore"
        assert not pol.get("_dynamic")

    def test_unknown_resolves_none(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JARVIS_AGENTS_PATHS", str(tmp_path))
        from tools.dispatch_agent import _resolve_policy
        assert _resolve_policy("does-not-exist") is None

    def test_user_agent_resolves_dynamic(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JARVIS_AGENTS_PATHS", str(tmp_path))
        _write_agent(tmp_path, "data-wrangler")
        from tools.dispatch_agent import _resolve_policy
        pol = _resolve_policy("data-wrangler")
        assert pol is not None
        assert pol["cli_agent"] == "data-wrangler"
        assert pol["_dynamic"] is True

    def test_builtin_wins_over_same_named_user_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JARVIS_AGENTS_PATHS", str(tmp_path))
        _write_agent(tmp_path, "researcher", "user override attempt")
        from tools.dispatch_agent import _resolve_policy
        pol = _resolve_policy("researcher")
        # The tuned built-in, not the dynamic synthesis.
        assert pol["ack"] == "Looking that up online…"
        assert not pol.get("_dynamic")


class TestArgvAndAck:
    def test_build_argv_targets_custom_name(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JARVIS_AGENTS_PATHS", str(tmp_path))
        _write_agent(tmp_path, "release-bot")
        from tools.dispatch_agent import _build_argv
        argv = _build_argv("release-bot", "cut the release notes")
        assert argv[-3:] == ["--agent", "release-bot", "cut the release notes"]

    def test_ack_phrase_for_custom(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JARVIS_AGENTS_PATHS", str(tmp_path))
        _write_agent(tmp_path, "release-bot")
        from tools.dispatch_agent import get_ack_phrase
        assert "release-bot" in (get_ack_phrase("release-bot") or "")
        assert get_ack_phrase("nope-nope") is None


# ── full handler ──────────────────────────────────────────────────────────


class TestHandlerWithCustomAgent:
    @pytest.mark.asyncio
    async def test_dispatch_custom_agent_returns_stdout(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JARVIS_AGENTS_PATHS", str(tmp_path))
        _write_agent(tmp_path, "summarizer")
        from tools.dispatch_agent import handle_dispatch_agent
        fake = _make_fake_proc(stdout=b"SUMMARY: all good\n", returncode=0)
        monkeypatch.setattr("asyncio.create_subprocess_exec", AsyncMock(return_value=fake))
        out = await handle_dispatch_agent({
            "subagent_type": "summarizer", "task": "summarize the log",
            "description": "summarize log",
        })
        assert "SUMMARY: all good" in out

    @pytest.mark.asyncio
    async def test_unknown_agent_error_lists_custom(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JARVIS_AGENTS_PATHS", str(tmp_path))
        _write_agent(tmp_path, "known-helper")
        from tools.dispatch_agent import handle_dispatch_agent
        out = await handle_dispatch_agent({
            "subagent_type": "ghost-agent", "task": "do a thing",
            "description": "ghost",
        })
        assert "unknown subagent_type" in out
        assert "known-helper" in out   # discovery folded into the error
