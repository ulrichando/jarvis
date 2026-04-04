"""Project onboarding state management."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import List


@dataclass
class Step:
    key: str
    text: str
    is_complete: bool
    is_completable: bool
    is_enabled: bool


def get_steps() -> List[Step]:
    """Get the current onboarding steps and their status."""
    cwd = os.getcwd()
    has_claude_md = (os.path.exists(os.path.join(cwd, "JARVIS.md")) or
                     os.path.exists(os.path.join(cwd, "CLAUDE.md")))
    is_workspace_dir_empty = not os.listdir(cwd) if os.path.isdir(cwd) else True

    return [
        Step(
            key="workspace",
            text="Ask JARVIS to create a new app or clone a repository",
            is_complete=False,
            is_completable=True,
            is_enabled=is_workspace_dir_empty,
        ),
        Step(
            key="claudemd",
            text="Run /init to create a JARVIS.md file with project instructions",
            is_complete=has_claude_md,
            is_completable=True,
            is_enabled=not is_workspace_dir_empty,
        ),
    ]


def is_project_onboarding_complete() -> bool:
    """Check if all completable and enabled onboarding steps are done."""
    return all(
        step.is_complete
        for step in get_steps()
        if step.is_completable and step.is_enabled
    )


def maybe_mark_project_onboarding_complete() -> None:
    """Mark project onboarding as complete if all steps are done."""
    if is_project_onboarding_complete():
        # In full implementation, would save to project config
        pass


@lru_cache(maxsize=1)
def should_show_project_onboarding() -> bool:
    """Check if project onboarding should be shown."""
    # In full implementation, would check project config
    return not is_project_onboarding_complete()


def increment_project_onboarding_seen_count() -> None:
    """Increment the count of how many times onboarding has been shown."""
    # In full implementation, would save to project config
    pass
