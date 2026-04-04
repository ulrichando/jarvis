"""Precondition checks for background remote sessions."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def check_needs_api_login() -> bool:
    """Check if user needs to log in with the API provider."""
    return False


async def check_is_git_clean() -> bool:
    """Check if git working directory is clean."""
    import subprocess
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            capture_output=True, text=True,
        )
        return result.returncode == 0 and not result.stdout.strip()
    except Exception:
        return False


async def check_has_remote_environment() -> bool:
    """Check if user has access to at least one remote environment."""
    return False


def check_is_in_git_repo() -> bool:
    """Check if current directory is inside a git repository."""
    import subprocess
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True,
        )
        return result.returncode == 0
    except Exception:
        return False


async def check_has_git_remote() -> bool:
    """Check if current repository has a remote configured."""
    import subprocess
    try:
        result = subprocess.run(
            ["git", "remote"],
            capture_output=True, text=True,
        )
        return result.returncode == 0 and bool(result.stdout.strip())
    except Exception:
        return False


async def check_github_app_installed(owner: str, repo: str) -> bool:
    """Check if GitHub app is installed on a specific repository."""
    return False
