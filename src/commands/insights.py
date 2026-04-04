"""Insights command - Analyze session data for patterns and insights."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Optional


async def get_prompt_for_command(args: str = "", **_kwargs: Any) -> list[dict[str, str]]:
    """Return the insights prompt."""
    return [
        {
            "type": "text",
            "text": (
                "Analyze the session data and provide insights about usage patterns, "
                "common tasks, and recommendations for improving workflow."
            ),
        }
    ]


command = {
    "type": "prompt",
    "name": "insights",
    "description": "Analyze session data for patterns and insights",
    "content_length": 0,
    "progress_message": "analyzing session data",
    "source": "builtin",
    "is_enabled": lambda: os.environ.get("USER_TYPE") == "ant",
    "get_prompt_for_command": get_prompt_for_command,
}
