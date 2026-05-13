"""Tests for `subagents/_isolation.py` — per-subagent worktree
isolation helpers.

These power `HandoffSubagent.isolation == "worktree"`: every
handoff to a spec with that flag spawns a fresh worktree, the
subagent operates inside it, and `task_done` auto-cleans.

Each test uses a real git repo in `tmp_path` so we exercise the
actual `git worktree add` / `remove` paths. Mocks would test
nothing useful — the whole value of this module is correctly
orchestrating git.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def tmp_repo(tmp_path):
    """Initialise a fresh git repo with one commit (so HEAD exists)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@e.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)
    (repo / "README.md").write_text("seed")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo


@pytest.fixture
def isolation(monkeypatch, tmp_repo):
    """`subagents._isolation` module + chdir into the tmp repo so
    `_repo_root()` resolves to it."""
    monkeypatch.chdir(tmp_repo)
    from subagents import _isolation
    return _isolation


# ── create_isolation_worktree ──────────────────────────────────


@pytest.mark.asyncio
async def test_create_makes_worktree_under_dot_worktrees(isolation, tmp_repo):
    wt = await isolation.create_isolation_worktree(
        subagent_name="validator", short_id="deadbeef",
    )
    assert wt is not None
    expected = tmp_repo / ".worktrees" / "validator-deadbeef"
    assert wt == expected
    # Real checkout
    assert (expected / "README.md").exists()


@pytest.mark.asyncio
async def test_create_assigns_unique_branch(isolation, tmp_repo):
    await isolation.create_isolation_worktree(
        subagent_name="validator", short_id="abc12345",
    )
    branches = subprocess.run(
        ["git", "branch", "--list", "worktree-validator-abc12345"],
        cwd=tmp_repo, capture_output=True, text=True,
    )
    assert "worktree-validator-abc12345" in branches.stdout


@pytest.mark.asyncio
async def test_create_auto_generates_short_id(isolation, tmp_repo):
    """When short_id is omitted, a fresh uuid-hex is used and the
    path varies between calls."""
    a = await isolation.create_isolation_worktree(subagent_name="validator")
    b = await isolation.create_isolation_worktree(subagent_name="validator")
    assert a is not None and b is not None
    assert a != b, "back-to-back calls must produce distinct paths"
    assert a.name.startswith("validator-")
    assert b.name.startswith("validator-")


@pytest.mark.asyncio
async def test_create_rejects_unsafe_name(isolation):
    """Path-traversal etc. in name → return None, don't try to git."""
    for bad in ("../escape", "with spaces", "with.dots", "-leading-dash", ""):
        wt = await isolation.create_isolation_worktree(subagent_name=bad)
        assert wt is None, f"unsafe name {bad!r} should yield None"


@pytest.mark.asyncio
async def test_create_outside_a_git_repo(monkeypatch, tmp_path):
    """Returns None cleanly when cwd isn't a git checkout —
    dispatch falls back to no-isolation."""
    not_a_repo = tmp_path / "no-git"
    not_a_repo.mkdir()
    monkeypatch.chdir(not_a_repo)
    from subagents import _isolation
    wt = await _isolation.create_isolation_worktree(subagent_name="x")
    assert wt is None


@pytest.mark.asyncio
async def test_create_existing_path_returns_none(isolation, tmp_repo):
    """If the target path is already there (e.g. leftover from a
    crashed prior run), don't overwrite — return None, log."""
    leftover = tmp_repo / ".worktrees" / "validator-collide"
    leftover.mkdir(parents=True)
    wt = await isolation.create_isolation_worktree(
        subagent_name="validator", short_id="collide",
    )
    assert wt is None


# ── cleanup_isolation_worktree ─────────────────────────────────


@pytest.mark.asyncio
async def test_cleanup_clean_worktree_removes_it(isolation, tmp_repo):
    wt = await isolation.create_isolation_worktree(
        subagent_name="validator", short_id="cleanme",
    )
    assert wt is not None and wt.exists()

    outcome = await isolation.cleanup_isolation_worktree(str(wt))
    assert "cleaned" in outcome
    assert not wt.exists()


@pytest.mark.asyncio
async def test_cleanup_leaves_dirty_worktree_alone(isolation, tmp_repo):
    """A worktree with modified tracked files must be kept; the
    outcome string flags it as DIRTY."""
    wt = await isolation.create_isolation_worktree(
        subagent_name="validator", short_id="dirty",
    )
    assert wt is not None

    # Dirty the worktree
    (wt / "README.md").write_text("modified by subagent")

    outcome = await isolation.cleanup_isolation_worktree(str(wt))
    assert "DIRTY" in outcome
    assert wt.exists(), "dirty worktree must be preserved for user review"


@pytest.mark.asyncio
async def test_cleanup_missing_path_returns_friendly_string(isolation):
    """Already-gone worktrees don't error — just acknowledge."""
    outcome = await isolation.cleanup_isolation_worktree(
        "/tmp/never-existed-2026-05-12-xyz",
    )
    assert "worktree gone already" in outcome


@pytest.mark.asyncio
async def test_cleanup_empty_path_no_op(isolation):
    outcome = await isolation.cleanup_isolation_worktree("")
    assert outcome == "no-worktree-to-clean"


# ── HandoffSubagent.isolation field ────────────────────────────


def test_handoff_spec_isolation_defaults_to_none():
    """Existing specs without `isolation` keep the no-isolation
    path — backwards-compat for the desktop/browser/screen_share
    subagents."""
    from subagents.registry import HandoffSubagent
    spec = HandoffSubagent(
        name="x", transfer_tool="transfer_to_x",
        when_to_use="", instructions="", tool_factory=lambda: [],
    )
    assert spec.isolation is None


def test_handoff_spec_accepts_worktree_isolation():
    from subagents.registry import HandoffSubagent
    spec = HandoffSubagent(
        name="y", transfer_tool="transfer_to_y",
        when_to_use="", instructions="", tool_factory=lambda: [],
        isolation="worktree",
    )
    assert spec.isolation == "worktree"
