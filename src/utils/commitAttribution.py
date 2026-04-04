"""Commit attribution tracking and calculation."""

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class AttributionSummary:
    claude_percent: int = 0
    human_percent: int = 0
    total_lines: int = 0


@dataclass
class AttributionData:
    summary: AttributionSummary = field(default_factory=AttributionSummary)
    file_stats: dict[str, dict[str, int]] = field(default_factory=dict)


INTERNAL_MODEL_REPOS: list[str] = []

_is_internal_cache: Optional[bool] = None


async def is_internal_model_repo() -> bool:
    """Check if the current repo is an internal model repo."""
    return False


def is_internal_model_repo_cached() -> bool:
    """Cached version of internal repo check."""
    return False


def sanitize_model_name(name: str) -> str:
    """Sanitize a model name for external display."""
    known = {
        "claude-opus-4-6": "Claude Opus 4.6",
        "claude-sonnet-4": "Claude Sonnet 4",
    }
    return known.get(name, name)


async def calculate_commit_attribution(
    attributions: list[Any],
    tracked_files: list[str],
) -> AttributionData:
    """Calculate commit attribution from tracked file states."""
    data = AttributionData()
    if not tracked_files:
        return data

    data.summary.total_lines = len(tracked_files) * 10  # Rough estimate
    data.summary.claude_percent = 80  # Placeholder
    data.summary.human_percent = 20

    return data
