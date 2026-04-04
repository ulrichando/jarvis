"""
System and user context for conversations.

Converted from context.ts -- provides git status, JARVIS.md content, and
other context prepended to each conversation.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import time
from datetime import date
from functools import lru_cache
from typing import Optional

logger = logging.getLogger(__name__)

MAX_STATUS_CHARS = 2000

# System prompt injection for cache breaking (ephemeral debugging state)
_system_prompt_injection: Optional[str] = None


def get_system_prompt_injection() -> Optional[str]:
    return _system_prompt_injection


def set_system_prompt_injection(value: Optional[str]) -> None:
    global _system_prompt_injection
    _system_prompt_injection = value
    # Clear context caches when injection changes
    get_user_context.cache_clear()
    get_system_context.cache_clear()


async def _exec_git(*args: str) -> str:
    """Run a git command and return stripped stdout, or empty string on failure."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        return stdout.decode().strip()
    except Exception:
        return ""


async def _get_is_git() -> bool:
    """Check if the current directory is a git repository."""
    result = await _exec_git("rev-parse", "--is-inside-work-tree")
    return result == "true"


@lru_cache(maxsize=1)
def _get_git_status_sync() -> Optional[str]:
    """Placeholder for the memoized async version -- called once per session."""
    return None


async def get_git_status() -> Optional[str]:
    """Get git status information for context."""
    if os.environ.get("NODE_ENV") == "test":
        return None

    start_time = time.monotonic()
    logger.debug("git_status_started")

    is_git = await _get_is_git()
    if not is_git:
        logger.debug("git_status_skipped_not_git duration_ms=%d", int((time.monotonic() - start_time) * 1000))
        return None

    try:
        branch, main_branch, status, log, user_name = await asyncio.gather(
            _exec_git("rev-parse", "--abbrev-ref", "HEAD"),
            _exec_git("config", "init.defaultBranch"),
            _exec_git("--no-optional-locks", "status", "--short"),
            _exec_git("--no-optional-locks", "log", "--oneline", "-n", "5"),
            _exec_git("config", "user.name"),
        )

        if not main_branch:
            main_branch = "main"

        # Truncate long status
        if len(status) > MAX_STATUS_CHARS:
            truncated_status = (
                status[:MAX_STATUS_CHARS]
                + "\n... (truncated because it exceeds 2k characters. "
                "Run 'git status' for more information)"
            )
        else:
            truncated_status = status

        parts = [
            "This is the git status at the start of the conversation. "
            "Note that this status is a snapshot in time, and will not "
            "update during the conversation.",
            f"Current branch: {branch}",
            f"Main branch (you will usually use this for PRs): {main_branch}",
        ]
        if user_name:
            parts.append(f"Git user: {user_name}")
        parts.extend([
            f"Status:\n{truncated_status or '(clean)'}",
            f"Recent commits:\n{log}",
        ])

        logger.debug(
            "git_status_completed duration_ms=%d truncated=%s",
            int((time.monotonic() - start_time) * 1000),
            len(status) > MAX_STATUS_CHARS,
        )
        return "\n\n".join(parts)

    except Exception as error:
        logger.error("git_status_failed: %s", error)
        return None


@lru_cache(maxsize=1)
async def get_system_context() -> dict[str, str]:
    """
    Context prepended to each conversation, cached for the duration.
    Includes git status and optional cache-breaking injection.
    """
    start_time = time.monotonic()
    logger.debug("system_context_started")

    git_status = await get_git_status()
    injection = get_system_prompt_injection()

    logger.debug(
        "system_context_completed duration_ms=%d has_git_status=%s has_injection=%s",
        int((time.monotonic() - start_time) * 1000),
        git_status is not None,
        injection is not None,
    )

    result: dict[str, str] = {}
    if git_status:
        result["gitStatus"] = git_status
    if injection:
        result["cacheBreaker"] = f"[CACHE_BREAKER: {injection}]"
    return result


def _get_local_iso_date() -> str:
    """Get today's date in ISO format."""
    return date.today().isoformat()


@lru_cache(maxsize=1)
async def get_user_context() -> dict[str, str]:
    """
    User context prepended to each conversation, cached for the duration.
    Includes JARVIS.md/CLAUDE.md content and current date.
    """
    start_time = time.monotonic()
    logger.debug("user_context_started")

    # In a full implementation, this would load JARVIS.md/CLAUDE.md files
    claude_md: Optional[str] = None

    logger.debug(
        "user_context_completed duration_ms=%d memory_length=%d",
        int((time.monotonic() - start_time) * 1000),
        len(claude_md) if claude_md else 0,
    )

    result: dict[str, str] = {}
    if claude_md:
        result["claudeMd"] = claude_md
    result["currentDate"] = f"Today's date is {_get_local_iso_date()}."
    return result
