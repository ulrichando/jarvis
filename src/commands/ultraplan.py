"""Ultraplan command - Multi-agent planning via remote session."""

from __future__ import annotations

import os
from typing import Any

# Multi-agent exploration timeout: 30 minutes
ULTRAPLAN_TIMEOUT_MS = 30 * 60 * 1000
CCR_TERMS_URL = "https://code.claude.com/docs/en/claude-code-on-the-web"

DEFAULT_INSTRUCTIONS = """You are a planning agent. Analyze the codebase and create a detailed implementation plan.

1. Understand the current architecture
2. Identify all files that need changes
3. Create a step-by-step plan with specific code changes
4. Consider edge cases and potential issues
5. Estimate complexity for each step"""


async def get_prompt_for_command(args: str = "", **_kwargs: Any) -> list[dict[str, str]]:
    """Return the ultraplan prompt."""
    user_instructions = args.strip() if args else ""
    prompt = DEFAULT_INSTRUCTIONS
    if user_instructions:
        prompt += f"\n\nUser instructions: {user_instructions}"
    return [{"type": "text", "text": prompt}]


def is_enabled() -> bool:
    """Check if ultraplan is enabled."""
    return os.environ.get("USER_TYPE") == "ant"


ultraplan = {
    "type": "prompt",
    "name": "ultraplan",
    "description": f"Multi-agent planning. Runs in JARVIS on the web. See {CCR_TERMS_URL}",
    "content_length": 0,
    "progress_message": "planning with multiple agents",
    "source": "builtin",
    "is_enabled": is_enabled,
    "get_prompt_for_command": get_prompt_for_command,
}
