"""Tests for `tools/worktree.py` — git-worktree management tools.

Each test sets up a fresh git repo in tmp_path so the real JARVIS
repo's worktrees aren't touched. Tests use real git commands (cheap,
sub-100ms each) rather than mocks because the tool's whole value is
correctly orchestrating git — mocking it out tests nothing useful.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def tmp_repo(tmp_path):
    """Initialise a brand-new git repo with one commit so HEAD is
    valid. Returns the repo root path."""
    repo = tmp_path / "repo"
    repo.mkdir()
    # Minimum viable init — name + email so git doesn't refuse the commit
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)
    (repo / "README.md").write_text("seed")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo


def _unwrap(tool):
    for attr in ("__livekit_agents_func", "_func", "fnc", "func", "callable"):
        f = getattr(tool, attr, None)
        if callable(f):
            return f
    if callable(tool):
        return tool
    raise RuntimeError(f"can't unwrap {tool!r}")


@pytest.fixture
def wt_module(monkeypatch, tmp_repo):
    """Get the worktree module + chdir into the tmp repo so
    `_repo_root()` resolves to it."""
    monkeypatch.chdir(tmp_repo)
    from tools import worktree
    return worktree


# ── enter_worktree ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_enter_creates_worktree_dir_and_branch(wt_module, tmp_repo):
    out = await _unwrap(wt_module.enter_worktree)(name="experiment-a")
    assert "experiment-a created" in out
    assert (tmp_repo / ".worktrees" / "experiment-a" / "README.md").exists()
    # Branch is named with the worktree- prefix
    assert "Branch: worktree-experiment-a" in out

    # The branch must exist in the parent repo
    branches = subprocess.run(
        ["git", "branch", "--list", "worktree-experiment-a"],
        cwd=tmp_repo, capture_output=True, text=True,
    )
    assert "worktree-experiment-a" in branches.stdout


@pytest.mark.asyncio
async def test_enter_auto_name_when_empty(wt_module, tmp_repo):
    out = await _unwrap(wt_module.enter_worktree)(name="")
    assert "created at" in out
    # Auto-name starts with "wt-" + a date stamp
    worktrees = list((tmp_repo / ".worktrees").iterdir())
    assert len(worktrees) == 1
    assert worktrees[0].name.startswith("wt-")


@pytest.mark.asyncio
async def test_enter_rejects_invalid_name(wt_module):
    for bad in ("path/traversal", "-leading-dash", "with spaces", "with.dot", ""):
        if bad == "":
            continue  # empty triggers auto-name, tested separately
        out = await _unwrap(wt_module.enter_worktree)(name=bad)
        assert "Invalid worktree name" in out or "is empty" in out, (
            f"bad name {bad!r} not rejected"
        )


@pytest.mark.asyncio
async def test_enter_rejects_path_traversal(wt_module):
    """Defensive: '..' in the name must not let the supervisor escape
    the .worktrees subdir."""
    out = await _unwrap(wt_module.enter_worktree)(name="../escape")
    assert "Invalid worktree name" in out


@pytest.mark.asyncio
async def test_enter_collides_when_dir_exists(wt_module, tmp_repo):
    """Second call with the same name must error cleanly — no
    silent overwrite."""
    await _unwrap(wt_module.enter_worktree)(name="dup")
    out = await _unwrap(wt_module.enter_worktree)(name="dup")
    assert "already exists" in out


@pytest.mark.asyncio
async def test_enter_with_base_branch(wt_module, tmp_repo):
    """Branching off a specified ref."""
    # Create a side branch with an extra commit so we can verify
    # the new worktree's HEAD matches the right ref.
    subprocess.run(["git", "checkout", "-q", "-b", "side"], cwd=tmp_repo, check=True)
    (tmp_repo / "side.txt").write_text("side")
    subprocess.run(["git", "add", "side.txt"], cwd=tmp_repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "side commit"], cwd=tmp_repo, check=True)
    subprocess.run(["git", "checkout", "-q", "main"], cwd=tmp_repo, check=True)

    out = await _unwrap(wt_module.enter_worktree)(name="from-side", base_branch="side")
    assert "created at" in out

    # The new worktree should contain side.txt (proves it's at side, not main)
    assert (tmp_repo / ".worktrees" / "from-side" / "side.txt").exists()


@pytest.mark.asyncio
async def test_enter_outside_a_git_repo(monkeypatch, tmp_path):
    """If cwd isn't inside any git repo, the tool refuses cleanly."""
    not_a_repo = tmp_path / "no-git-here"
    not_a_repo.mkdir()
    monkeypatch.chdir(not_a_repo)
    from tools import worktree
    out = await _unwrap(worktree.enter_worktree)(name="x")
    assert "Not inside a git repository" in out


# ── exit_worktree ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_exit_removes_clean_worktree(wt_module, tmp_repo):
    await _unwrap(wt_module.enter_worktree)(name="clean-exit")
    out = await _unwrap(wt_module.exit_worktree)(name="clean-exit")
    assert "removed" in out
    assert not (tmp_repo / ".worktrees" / "clean-exit").exists()


@pytest.mark.asyncio
async def test_exit_missing_worktree(wt_module):
    out = await _unwrap(wt_module.exit_worktree)(name="never-existed")
    assert "No worktree" in out


@pytest.mark.asyncio
async def test_exit_refuses_dirty_without_force(wt_module, tmp_repo):
    """Dirty = a modified tracked file. Must refuse + nudge the user."""
    await _unwrap(wt_module.enter_worktree)(name="dirty")
    # Dirty the worktree's tracked file
    wt = tmp_repo / ".worktrees" / "dirty"
    (wt / "README.md").write_text("modified")

    out = await _unwrap(wt_module.exit_worktree)(name="dirty")
    assert "dirty" in out.lower()
    assert "force=True" in out
    # Worktree still exists — no silent destruction
    assert wt.exists()


@pytest.mark.asyncio
async def test_exit_force_removes_dirty(wt_module, tmp_repo):
    await _unwrap(wt_module.enter_worktree)(name="force-me")
    wt = tmp_repo / ".worktrees" / "force-me"
    (wt / "README.md").write_text("modified")

    out = await _unwrap(wt_module.exit_worktree)(name="force-me", force=True)
    assert "removed" in out
    assert not wt.exists()


@pytest.mark.asyncio
async def test_exit_leaves_branch_behind(wt_module, tmp_repo):
    """Branch survives the worktree removal — the user can keep it
    for a PR or delete it manually. Voice JARVIS doesn't decide for
    them."""
    await _unwrap(wt_module.enter_worktree)(name="keep-branch")
    await _unwrap(wt_module.exit_worktree)(name="keep-branch")
    branches = subprocess.run(
        ["git", "branch", "--list", "worktree-keep-branch"],
        cwd=tmp_repo, capture_output=True, text=True,
    )
    assert "worktree-keep-branch" in branches.stdout, (
        f"branch was unexpectedly deleted: {branches.stdout!r}"
    )


@pytest.mark.asyncio
async def test_exit_rejects_invalid_name(wt_module):
    out = await _unwrap(wt_module.exit_worktree)(name="../escape")
    assert "Invalid worktree name" in out


# ── list_worktrees ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_includes_main_and_created(wt_module, tmp_repo):
    await _unwrap(wt_module.enter_worktree)(name="alpha")
    await _unwrap(wt_module.enter_worktree)(name="beta")
    out = await _unwrap(wt_module.list_worktrees)()
    # Main worktree always present
    assert str(tmp_repo) in out
    # Both created worktrees show up
    assert ".worktrees/alpha" in out
    assert ".worktrees/beta" in out
    # Branch names visible
    assert "worktree-alpha" in out
    assert "worktree-beta" in out


@pytest.mark.asyncio
async def test_list_outside_a_git_repo(monkeypatch, tmp_path):
    not_a_repo = tmp_path / "no-git-here"
    not_a_repo.mkdir()
    monkeypatch.chdir(not_a_repo)
    from tools import worktree
    out = await _unwrap(worktree.list_worktrees)()
    assert "Not inside a git repository" in out
