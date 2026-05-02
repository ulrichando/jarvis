"""Tests for the GitHub connector subagent. Smoke-test the registration
and graceful-degrade paths; live `gh` calls are skipped when not
authenticated."""
import os
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import jarvis_github


_GH_AUTHED = bool(shutil.which("gh")) and (
    Path.home() / ".config" / "gh" / "hosts.yml"
).exists()


def test_is_available_reflects_gh_state():
    """is_available() should match the gh CLI presence + auth state."""
    assert jarvis_github.is_available() == _GH_AUTHED


def test_github_subagent_registered():
    from specialists.registry import clear_subagents, SUBAGENT_REGISTRY
    clear_subagents()
    from specialists.github import register_github
    register_github()
    assert "github" in SUBAGENT_REGISTRY
    spec = SUBAGENT_REGISTRY["github"]
    assert spec.name == "github"
    assert spec.enabled == _GH_AUTHED


def test_github_factory_builds():
    from specialists.registry import clear_subagents
    clear_subagents()
    from specialists.github import register_github, _github_tools
    register_github()
    tools = _github_tools()
    assert isinstance(tools, list) and len(tools) == 4
    names = sorted(getattr(getattr(t, "_func", t), "__name__", "") for t in tools)
    assert names == [
        "github_list_issues",
        "github_list_prs",
        "github_view_issue",
        "github_view_pr",
    ]


def test_format_short_user_strips_at():
    assert jarvis_github._format_short_user("@ulrichando") == "ulrichando"
    assert jarvis_github._format_short_user("ulrichando") == "ulrichando"


def test_returns_offline_message_when_gh_missing(monkeypatch):
    """If `gh` isn't on PATH, every tool returns a graceful error."""
    monkeypatch.setattr(jarvis_github, "_gh_path", lambda: None)
    import asyncio
    fn = jarvis_github.github_list_prs._func
    result = asyncio.run(fn(repo="", state="open", limit=5))
    assert "not installed" in result


def test_invalid_state_rejected():
    import asyncio
    fn = jarvis_github.github_list_prs._func
    result = asyncio.run(fn(repo="", state="bogus", limit=5))
    assert "invalid state" in result.lower()


def test_subagent_is_read_only():
    """v1 must NOT expose write tools (comment, merge, close)."""
    from specialists.github import _github_tools
    tools = _github_tools()
    names = [getattr(getattr(t, "_func", t), "__name__", "") for t in tools]
    forbidden = ("github_comment", "github_merge_pr", "github_close_issue")
    for name in forbidden:
        assert name not in names, f"v1 should not expose write tool {name}"


@pytest.mark.skipif(not _GH_AUTHED, reason="gh CLI not authenticated")
def test_live_list_prs_returns_string():
    """Live integration test — only runs when gh is authed. Just
    asserts the tool returns a non-error string from a real API call."""
    import asyncio
    fn = jarvis_github.github_list_prs._func
    result = asyncio.run(fn(repo="", state="open", limit=2))
    # Should NOT contain "(github error" — should either be an empty
    # response or a list.
    assert "github error" not in result.lower(), result
