"""Cleanup utilities for old message files and caches."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_CLEANUP_PERIOD_DAYS = 30


@dataclass
class CleanupResult:
    messages: int = 0
    errors: int = 0


def add_cleanup_results(a: CleanupResult, b: CleanupResult) -> CleanupResult:
    return CleanupResult(
        messages=a.messages + b.messages,
        errors=a.errors + b.errors,
    )


def convert_file_name_to_date(filename: str) -> datetime:
    """Convert a filename with timestamp to a datetime."""
    base = filename.split(".")[0]
    iso_str = re.sub(
        r"T(\d{2})-(\d{2})-(\d{2})-(\d{3})Z",
        r"T\1:\2:\3.\4Z",
        base,
    )
    return datetime.fromisoformat(iso_str.replace("Z", "+00:00"))


async def cleanup_old_message_files() -> CleanupResult:
    """Clean up old message and error log files."""
    return CleanupResult()


async def cleanup_old_message_files_in_background() -> None:
    """Clean up old message files in the background."""
    try:
        await cleanup_old_message_files()
    except Exception as e:
        logger.error(f"Background cleanup failed: {e}")


async def cleanup_old_versions_throttled() -> None:
    """Clean up old versions with throttling."""
    pass


async def cleanup_npm_cache_for_anthropic_packages() -> None:
    """Clean up npm cache for anthropic packages."""
    pass
