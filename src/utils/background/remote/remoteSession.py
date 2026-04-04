"""Background remote session management."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional, Union


@dataclass
class BackgroundRemoteSession:
    id: str
    command: str
    start_time: int
    status: Literal["starting", "running", "completed", "failed", "killed"]
    title: str
    type: str = "remote_session"
    todo_list: dict[str, Any] = field(default_factory=dict)
    log: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class NotLoggedIn:
    type: str = "not_logged_in"


@dataclass
class NoRemoteEnvironment:
    type: str = "no_remote_environment"


@dataclass
class NotInGitRepo:
    type: str = "not_in_git_repo"


@dataclass
class NoGitRemote:
    type: str = "no_git_remote"


@dataclass
class GithubAppNotInstalled:
    type: str = "github_app_not_installed"


@dataclass
class PolicyBlocked:
    type: str = "policy_blocked"


BackgroundRemoteSessionPrecondition = Union[
    NotLoggedIn, NoRemoteEnvironment, NotInGitRepo,
    NoGitRemote, GithubAppNotInstalled, PolicyBlocked,
]


async def check_background_remote_session_eligibility(
    skip_bundle: bool = False,
) -> list[BackgroundRemoteSessionPrecondition]:
    """Check eligibility for creating a background remote session.

    Returns empty list if all checks passed.
    """
    from .preconditions import (
        check_has_remote_environment,
        check_is_in_git_repo,
        check_needs_claude_ai_login,
    )

    errors: list[BackgroundRemoteSessionPrecondition] = []

    needs_login = await check_needs_claude_ai_login()
    if needs_login:
        errors.append(NotLoggedIn())

    has_env = await check_has_remote_environment()
    if not has_env:
        errors.append(NoRemoteEnvironment())

    if not check_is_in_git_repo():
        errors.append(NotInGitRepo())

    return errors
