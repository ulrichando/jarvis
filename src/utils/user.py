"""
Core user data utilities for analytics and identity.
"""

import logging
import os
import subprocess
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_cached_email: Optional[str] = None
_email_fetched = False


@dataclass
class GitHubActionsMetadata:
    actor: Optional[str] = None
    actor_id: Optional[str] = None
    repository: Optional[str] = None
    repository_id: Optional[str] = None
    repository_owner: Optional[str] = None
    repository_owner_id: Optional[str] = None


@dataclass
class CoreUserData:
    device_id: str = ""
    session_id: str = ""
    email: Optional[str] = None
    app_version: str = "0.1.0"
    platform: str = "linux"
    organization_uuid: Optional[str] = None
    account_uuid: Optional[str] = None
    user_type: Optional[str] = None
    subscription_type: Optional[str] = None
    rate_limit_tier: Optional[str] = None
    first_token_time: Optional[int] = None
    github_actions_metadata: Optional[GitHubActionsMetadata] = None


async def init_user() -> None:
    """Initialize user data asynchronously. Should be called early in startup."""
    global _cached_email, _email_fetched
    if not _email_fetched:
        _cached_email = await _get_email_async()
        _email_fetched = True


def reset_user_cache() -> None:
    """Reset all user data caches."""
    global _cached_email, _email_fetched
    _cached_email = None
    _email_fetched = False
    get_core_user_data.cache_clear()
    get_git_email.cache_clear()


@lru_cache(maxsize=2)
def get_core_user_data(include_analytics: bool = False) -> CoreUserData:
    """Get core user data."""
    return CoreUserData(
        device_id=os.environ.get("JARVIS_USER_ID", ""),
        session_id=os.environ.get("JARVIS_SESSION_ID", ""),
        email=_get_email(),
        platform=_get_platform(),
        user_type=os.environ.get("USER_TYPE"),
    )


def _get_email() -> Optional[str]:
    """Get user email synchronously."""
    if _cached_email is not None:
        return _cached_email
    return None


async def _get_email_async() -> Optional[str]:
    """Get user email asynchronously (tries git config)."""
    return get_git_email()


@lru_cache(maxsize=1)
def get_git_email() -> Optional[str]:
    """Get the user's git email from git config."""
    try:
        result = subprocess.run(
            ["git", "config", "--get", "user.email"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def _get_platform() -> str:
    """Get the current platform."""
    import sys
    if sys.platform == "darwin":
        return "macos"
    elif sys.platform == "win32":
        return "windows"
    return "linux"
