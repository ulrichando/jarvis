"""Cross-project resume utilities."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional, Union


@dataclass
class SameProject:
    is_cross_project: bool = False


@dataclass
class SameRepoWorktree:
    is_cross_project: bool = True
    is_same_repo_worktree: bool = True
    project_path: str = ""


@dataclass
class DifferentProject:
    is_cross_project: bool = True
    is_same_repo_worktree: bool = False
    command: str = ""
    project_path: str = ""


CrossProjectResumeResult = Union[SameProject, SameRepoWorktree, DifferentProject]


def check_cross_project_resume(
    log_project_path: Optional[str],
    current_cwd: str,
    show_all_projects: bool,
    worktree_paths: list[str],
    session_id: str = "",
) -> CrossProjectResumeResult:
    """Check if a log is from a different project directory."""
    if not show_all_projects or not log_project_path or log_project_path == current_cwd:
        return SameProject()

    # Check if log path is under a worktree of the same repo
    is_same_repo = any(
        log_project_path == wt or log_project_path.startswith(wt + os.sep)
        for wt in worktree_paths
    )

    if is_same_repo:
        return SameRepoWorktree(project_path=log_project_path)

    command = f"cd {log_project_path} && jarvis --resume {session_id}"
    return DifferentProject(command=command, project_path=log_project_path)
