"""
Git worktree management utilities.

Provides functions for creating, managing, and cleaning up git worktrees
used for parallel development in agent swarms.
"""

import logging
import os
import re
import subprocess
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

VALID_WORKTREE_SLUG_SEGMENT = re.compile(r"^[a-zA-Z0-9._-]+$")
MAX_WORKTREE_SLUG_LENGTH = 64


def validate_worktree_slug(slug: str) -> None:
    """
    Validates a worktree slug to prevent path traversal and directory escape.

    Forward slashes are allowed for nesting (e.g. 'asm/feature-foo'); each
    segment is validated independently.

    Raises:
        ValueError: If the slug is invalid.
    """
    if len(slug) > MAX_WORKTREE_SLUG_LENGTH:
        raise ValueError(
            f"Invalid worktree name: must be {MAX_WORKTREE_SLUG_LENGTH} "
            f"characters or fewer (got {len(slug)})"
        )

    for segment in slug.split("/"):
        if segment in (".", ".."):
            raise ValueError(
                f'Invalid worktree name "{slug}": must not contain '
                '"." or ".." path segments'
            )
        if not VALID_WORKTREE_SLUG_SEGMENT.match(segment):
            raise ValueError(
                f'Invalid worktree name "{slug}": each "/"-separated segment '
                "must be non-empty and contain only letters, digits, dots, "
                "underscores, and dashes"
            )


async def create_worktree(
    repo_root: str,
    worktree_name: str,
    branch_name: Optional[str] = None,
    base_branch: Optional[str] = None,
) -> str:
    """
    Create a new git worktree.

    Args:
        repo_root: Path to the main repository root.
        worktree_name: Name/slug for the worktree directory.
        branch_name: Branch name to create (defaults to worktree_name).
        base_branch: Branch to base the new worktree on.

    Returns:
        Path to the created worktree directory.

    Raises:
        ValueError: If the worktree slug is invalid.
        RuntimeError: If worktree creation fails.
    """
    validate_worktree_slug(worktree_name)

    if branch_name is None:
        branch_name = worktree_name

    worktree_path = os.path.join(
        repo_root, ".claude", "worktrees", worktree_name
    )

    os.makedirs(os.path.dirname(worktree_path), exist_ok=True)

    cmd = ["git", "worktree", "add"]
    if base_branch:
        cmd.extend(["-b", branch_name, worktree_path, base_branch])
    else:
        cmd.extend(["-b", branch_name, worktree_path])

    result = subprocess.run(
        cmd,
        cwd=repo_root,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to create worktree: {result.stderr.strip()}"
        )

    logger.info(f"Created worktree at {worktree_path} on branch {branch_name}")
    return worktree_path


async def remove_worktree(
    repo_root: str,
    worktree_path: str,
    force: bool = False,
) -> None:
    """
    Remove a git worktree.

    Args:
        repo_root: Path to the main repository root.
        worktree_path: Path to the worktree to remove.
        force: If True, force removal even with uncommitted changes.
    """
    cmd = ["git", "worktree", "remove"]
    if force:
        cmd.append("--force")
    cmd.append(worktree_path)

    result = subprocess.run(
        cmd,
        cwd=repo_root,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        logger.warning(f"Failed to remove worktree: {result.stderr.strip()}")
    else:
        logger.info(f"Removed worktree at {worktree_path}")


async def list_worktrees(repo_root: str) -> List[dict]:
    """
    List all git worktrees for a repository.

    Returns:
        List of dicts with 'path', 'branch', and 'head' keys.
    """
    result = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        return []

    worktrees: List[dict] = []
    current: dict = {}

    for line in result.stdout.split("\n"):
        line = line.strip()
        if not line:
            if current:
                worktrees.append(current)
                current = {}
            continue

        if line.startswith("worktree "):
            current["path"] = line[len("worktree "):]
        elif line.startswith("HEAD "):
            current["head"] = line[len("HEAD "):]
        elif line.startswith("branch "):
            current["branch"] = line[len("branch "):]

    if current:
        worktrees.append(current)

    return worktrees


async def symlink_directories(
    repo_root_path: str,
    worktree_path: str,
    dirs_to_symlink: List[str],
) -> None:
    """
    Symlinks directories from the main repository to avoid duplication.
    Prevents disk bloat from duplicating node_modules and similar large directories.
    """
    for dir_name in dirs_to_symlink:
        source = os.path.join(repo_root_path, dir_name)
        target = os.path.join(worktree_path, dir_name)

        if not os.path.exists(source):
            continue

        if os.path.exists(target) or os.path.islink(target):
            continue

        try:
            os.symlink(source, target)
            logger.debug(f"Symlinked {source} -> {target}")
        except OSError as e:
            logger.warning(f"Failed to symlink {dir_name}: {e}")
