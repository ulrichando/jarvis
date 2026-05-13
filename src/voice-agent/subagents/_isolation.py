"""Per-subagent worktree isolation — internal plumbing for
`HandoffSubagent.isolation == "worktree"`.

Two helpers:

  - `create_isolation_worktree(subagent_name)` runs `git worktree
    add <repo>/.worktrees/<name>-<short_id> -b worktree-<name>-<short_id>`
    from the JARVIS repo root and returns the absolute path of the
    new worktree. Returns None on any failure (no git, not in a
    repo, name collision) — the dispatch path falls back to running
    the subagent without isolation rather than aborting the handoff.

  - `cleanup_isolation_worktree(worktree_path)` runs `git worktree
    remove <path>` on a clean worktree, or logs and leaves a dirty
    one alone. Returns a one-line summary string suitable for the
    subagent log.

Both functions shell out via `asyncio.create_subprocess_exec("git",
...)` so they fit naturally inside the async `_transfer` and
`task_done` codepaths.

Worktree naming: `<subagent_name>-<8-hex-short-uuid>` so the same
subagent can be invoked many times per session without name
collision. Both directory AND branch use the same suffix so the
branch is easy to identify post-hoc.

The cleanup is BEST EFFORT. If git refuses the remove (dirty,
locked, etc.), the worktree stays on disk and the user can clean
it up manually via `git worktree remove <path>` or `git worktree
remove --force`. We deliberately don't force-cleanup — that would
discard the subagent's work without consent.
"""
from __future__ import annotations

import asyncio
import logging
import re
import uuid
from pathlib import Path
from typing import Optional


__all__ = [
    "create_isolation_worktree",
    "cleanup_isolation_worktree",
]


_logger = logging.getLogger("jarvis.subagents._isolation")


_WORKTREES_SUBDIR = ".worktrees"
_BRANCH_PREFIX = "worktree-"

# Subagent names that drive this should be lower-kebab. Validate
# defensively in case a spec author passes something weird.
_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]{0,63}$")


async def _git(*args: str, cwd: Optional[str] = None) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        "git", *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    out_b, err_b = await proc.communicate()
    return (
        proc.returncode or 0,
        out_b.decode("utf-8", "replace"),
        err_b.decode("utf-8", "replace"),
    )


async def _repo_root() -> Optional[Path]:
    rc, out, _ = await _git("rev-parse", "--show-toplevel")
    if rc != 0:
        return None
    txt = out.strip()
    return Path(txt) if txt else None


def _short_id() -> str:
    """8 hex chars from a fresh uuid4. ~64 bits of entropy — plenty
    for per-session collision avoidance."""
    return uuid.uuid4().hex[:8]


async def create_isolation_worktree(
    subagent_name: str,
    *,
    short_id: Optional[str] = None,
) -> Optional[Path]:
    """Create `<repo>/.worktrees/<name>-<short_id>/` on branch
    `worktree-<name>-<short_id>`. Returns the absolute path of the
    worktree on success, None on any failure (logged at WARNING).

    The `short_id` parameter is exposed for tests (deterministic
    runs); production always omits it and gets a fresh uuid hex.
    """
    if not _SAFE_NAME_RE.match(subagent_name or ""):
        _logger.warning(
            f"[isolation] refusing unsafe subagent name: {subagent_name!r}"
        )
        return None

    root = await _repo_root()
    if root is None:
        _logger.warning(
            "[isolation] not inside a git repo — running without isolation"
        )
        return None

    sid = short_id or _short_id()
    name = f"{subagent_name}-{sid}"
    wt_path = root / _WORKTREES_SUBDIR / name
    branch = f"{_BRANCH_PREFIX}{name}"

    if wt_path.exists():
        _logger.warning(
            f"[isolation] {wt_path} already exists — running without isolation"
        )
        return None

    rc, _out, err = await _git(
        "worktree", "add", str(wt_path), "-b", branch,
        cwd=str(root),
    )
    if rc != 0:
        _logger.warning(
            f"[isolation] worktree add failed for {subagent_name}: "
            f"{err.strip() or 'unknown'}"
        )
        return None

    _logger.info(
        f"[isolation] {subagent_name} → worktree {wt_path} (branch {branch})"
    )
    return wt_path


async def cleanup_isolation_worktree(worktree_path: str) -> str:
    """Remove the worktree at `worktree_path` IF it's clean. Returns
    a one-line summary for logging — never raises.

    Clean case: `git worktree remove <path>` succeeds → worktree
    directory disappears, branch survives (the user may want to PR
    its committed work).

    Dirty case: git refuses with "is dirty" or similar → we log + leave
    it. Manual cleanup via `git worktree remove --force <path>`
    discards the subagent's uncommitted work; we don't auto-do that.
    """
    if not worktree_path:
        return "no-worktree-to-clean"
    wt = Path(worktree_path)
    if not wt.exists():
        return f"worktree gone already: {worktree_path}"

    root = await _repo_root()
    cwd = str(root) if root is not None else None

    rc, _out, err = await _git(
        "worktree", "remove", str(wt),
        cwd=cwd,
    )
    if rc == 0:
        _logger.info(f"[isolation] cleaned {wt}")
        return f"cleaned {wt}"

    err_low = err.lower().strip()
    if (
        "is dirty" in err_low
        or "contains modified" in err_low
        or "untracked" in err_low
        or "not empty" in err_low
    ):
        _logger.warning(
            f"[isolation] worktree {wt} is DIRTY — leaving it for manual cleanup. "
            f"git: {err.strip()!r}"
        )
        return (
            f"DIRTY: kept {wt} for review (commit or `git worktree remove "
            f"--force {wt}` to discard)"
        )

    _logger.warning(
        f"[isolation] worktree remove failed for {wt}: {err.strip()!r}"
    )
    return f"FAILED: {err.strip() or 'unknown error'}"
