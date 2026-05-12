"""Git-worktree management — voice-adapted port of claude-code's
EnterWorktree / ExitWorktree.

JARVIS exposes three @function_tools so the supervisor can spin up
isolated git worktrees mid-session for try-this-in-a-branch work
without polluting the user's main checkout:

  - enter_worktree(name, base_branch)
    `git worktree add <repo>/.worktrees/<name> -b <branch>` against
    the current branch (or `base_branch` if provided). Returns the
    absolute worktree path + branch name so the supervisor knows
    where to operate.

  - exit_worktree(name, force)
    `git worktree remove <path>`. Refuses to remove a dirty worktree
    (uncommitted / untracked files) unless `force=True`. The branch
    is left behind by default — the user can clean it up separately
    once they've decided whether to keep the work.

  - list_worktrees()
    `git worktree list` — every worktree of this repo, with its
    branch and HEAD short-sha.

**Repo discovery:** runs `git -C <cwd> rev-parse --show-toplevel`
from the voice-agent's working directory; that's the JARVIS repo
root in production.

**Naming convention:** matches the repo's existing
`<repo>/.worktrees/<name>/` layout (6 worktrees already live there
pre-2026-05-12). Claude-code's variant uses `<repo>/.claude/
worktrees/`; we deliberately diverge to avoid creating a parallel
storage structure right next to one that already works.

**State coupling:** none. The tool creates / removes worktrees via
git's native machinery; we don't track an "active worktree" field
or auto-switch bash()'s cwd. The supervisor uses absolute paths or
`cd <wt-path> && cmd` patterns to operate inside a worktree, same
as a human would. Keeps the tool's blast radius small.

**Not implemented:**
  - `.worktreeinclude` whitelist for untracked-file copy (claude-
    code has this — voice can shell out to `cp` if specific files
    are needed).
  - Per-subagent `isolation: worktree` spec field (the user TODO
    item — applies to HandoffSubagent dispatch, separate change).
  - Branch deletion on exit (left manual — the user might want the
    branch for a PR even after the worktree is gone).
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from pathlib import Path
from typing import Optional

from livekit.agents.llm import function_tool


__all__ = ["enter_worktree", "exit_worktree", "list_worktrees"]


_logger = logging.getLogger("jarvis.tools.worktree")


# Worktrees live at <repo-root>/.worktrees/<name>/ — matches the
# existing convention. Branch defaults to "worktree-<name>".
_WORKTREES_SUBDIR = ".worktrees"
_BRANCH_PREFIX = "worktree-"

# Names must be filesystem + git-ref safe: ascii letters, digits,
# hyphens, underscores. No leading dash, no path traversal.
_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]{0,63}$")


async def _git(*args: str, cwd: Optional[str] = None) -> tuple[int, str, str]:
    """Run `git <args>` and return (returncode, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        "git", *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    out_b, err_b = await proc.communicate()
    return proc.returncode or 0, out_b.decode("utf-8", "replace"), err_b.decode("utf-8", "replace")


async def _repo_root(start: Optional[str] = None) -> Optional[Path]:
    """Resolve the git repo root from `start` (or cwd)."""
    rc, out, _ = await _git(
        "rev-parse", "--show-toplevel",
        cwd=start or None,
    )
    if rc != 0:
        return None
    root = out.strip()
    if not root:
        return None
    return Path(root)


def _validate_name(name: str) -> Optional[str]:
    """Return an error string if invalid, else None."""
    if not name:
        return "Name is empty."
    if not _NAME_RE.match(name):
        return (
            f"Invalid worktree name {name!r}. Allowed: ascii letters / digits / "
            f"`-` / `_`, 1-64 chars, no leading dash. (Lower-kebab is conventional.)"
        )
    return None


def _auto_name() -> str:
    """Generate a fallback name when the caller doesn't supply one.

    Timestamp-based, sortable, non-clashing within a second's
    granularity. Claude-code uses adjective-adjective-animal; voice
    JARVIS keeps it boring + chronological.
    """
    return f"wt-{time.strftime('%Y%m%d-%H%M%S', time.localtime())}"


# ── @function_tool surface ──────────────────────────────────────


@function_tool
async def enter_worktree(name: str = "", base_branch: str = "") -> str:
    """Create a git worktree at `<repo>/.worktrees/<name>/` on a
    fresh branch.

    Use for try-this-in-a-branch experiments mid-session: the user
    asks "try the fix on a separate branch first," or you (the
    supervisor) want to run a destructive operation in isolation
    without touching the main checkout. The worktree is a full git
    checkout sharing the parent repo's `.git/objects` — cheap to
    create, cheap to throw away.

    Args:
        name:        Worktree name. Used for BOTH the directory
                     (`<repo>/.worktrees/<name>/`) AND the branch
                     (`worktree-<name>`). Must be ASCII letters /
                     digits / `-` / `_`, 1-64 chars. If empty, a
                     timestamped name like `wt-20260512-235901` is
                     auto-generated.
        base_branch: Optional branch (or any git ref) to branch from.
                     Empty → current HEAD.

    Returns:
        Worktree path + branch name on success, or an error string.
    """
    nm = (name or "").strip() or _auto_name()
    err = _validate_name(nm)
    if err is not None:
        return err
    base = (base_branch or "").strip()

    root = await _repo_root()
    if root is None:
        return "Not inside a git repository — can't create a worktree."

    wt_path = root / _WORKTREES_SUBDIR / nm
    branch = f"{_BRANCH_PREFIX}{nm}"

    if wt_path.exists():
        return (
            f"Worktree path {wt_path} already exists. Pick a different name "
            f"or call exit_worktree first."
        )

    args = ["worktree", "add", str(wt_path), "-b", branch]
    if base:
        args.append(base)

    rc, out, err_text = await _git(*args, cwd=str(root))
    if rc != 0:
        return f"git worktree add failed: {err_text.strip() or out.strip() or 'unknown error'}"

    _logger.info(f"[worktree] created {nm} at {wt_path} (branch {branch})")
    return (
        f"Worktree {nm} created at {wt_path}\n"
        f"Branch: {branch} (off {base or 'current HEAD'})\n"
        f"Operate inside with `cd {wt_path} && ...` patterns or absolute paths."
    )


@function_tool
async def exit_worktree(name: str, force: bool = False) -> str:
    """Remove a worktree directory previously created with
    `enter_worktree`.

    Refuses to remove a worktree with uncommitted changes (modified
    tracked files OR untracked / staged files) unless `force=True`.
    The branch is left behind — the user can keep it for a PR or
    delete it manually with `git branch -D <branch>`.

    Args:
        name:  The worktree name from enter_worktree (NOT the full
               path). The same string used to create it.
        force: If True, removes a dirty worktree (discards
               uncommitted changes). Default False — refuses dirty.

    Returns:
        Confirmation, or an error explaining why the remove was
        refused.
    """
    nm = (name or "").strip()
    err = _validate_name(nm)
    if err is not None:
        return err

    root = await _repo_root()
    if root is None:
        return "Not inside a git repository."

    wt_path = root / _WORKTREES_SUBDIR / nm
    if not wt_path.exists():
        return f"No worktree at {wt_path}. Call list_worktrees to see what's active."

    args = ["worktree", "remove", str(wt_path)]
    if force:
        args.append("--force")

    rc, out, err_text = await _git(*args, cwd=str(root))
    if rc != 0:
        msg = err_text.strip() or out.strip() or "unknown error"
        # Git's specific dirty-worktree message — surface a clearer hint.
        if "is dirty" in msg or "contains modified" in msg or "is not empty" in msg:
            return (
                f"Refusing to remove {nm} — worktree is dirty (uncommitted or "
                f"untracked files). Commit / stash / discard there, OR call "
                f"`exit_worktree(name={nm!r}, force=True)` to discard the changes."
            )
        return f"git worktree remove failed: {msg}"

    _logger.info(f"[worktree] removed {nm} (force={force})")
    branch = f"{_BRANCH_PREFIX}{nm}"
    return (
        f"Worktree {nm} removed. Branch {branch} left intact — delete with "
        f"`git branch -D {branch}` if you don't want it."
    )


@function_tool
async def list_worktrees() -> str:
    """List every git worktree of this repository.

    Returns one row per worktree: absolute path, short HEAD sha, and
    the branch (or `(detached)` if checked out at a commit). Mirrors
    `git worktree list --porcelain` reshaped for voice readability.
    """
    root = await _repo_root()
    if root is None:
        return "Not inside a git repository."

    rc, out, err_text = await _git("worktree", "list", cwd=str(root))
    if rc != 0:
        return f"git worktree list failed: {err_text.strip() or 'unknown error'}"

    lines = [ln for ln in out.splitlines() if ln.strip()]
    if not lines:
        return "No worktrees."
    return f"{len(lines)} worktree(s):\n" + "\n".join(f"  {ln}" for ln in lines)
