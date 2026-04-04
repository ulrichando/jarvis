"""Memory version utilities: git repo status checking."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


def _find_git_root(path: str) -> Optional[str]:
    """Walk up from path looking for .git directory."""
    current = os.path.abspath(path)
    while True:
        git_dir = os.path.join(current, ".git")
        if os.path.exists(git_dir):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def project_is_in_git_repo(cwd: str) -> bool:
    """Check if the given directory is inside a git repository."""
    return _find_git_root(cwd) is not None
