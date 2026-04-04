"""Statusline command - Set up status line UI."""

from __future__ import annotations

from typing import Any

AGENT_TOOL_NAME = "Agent"


async def get_prompt_for_command(args: str = "", **_kwargs: Any) -> list[dict[str, str]]:
    """Return the statusline setup prompt."""
    prompt = args.strip() or "Configure my statusLine from my shell PS1 configuration"
    return [
        {
            "type": "text",
            "text": f'Create an {AGENT_TOOL_NAME} with subagent_type "statusline-setup" and the prompt "{prompt}"',
        }
    ]


statusline = {
    "type": "prompt",
    "name": "statusline",
    "description": "Set up JARVIS status line UI",
    "content_length": 0,
    "aliases": [],
    "progress_message": "setting up statusLine",
    "allowed_tools": [AGENT_TOOL_NAME, "Read(~/**)", "Edit(~/.jarvis/settings.json)"],
    "source": "builtin",
    "disable_non_interactive": True,
    "get_prompt_for_command": get_prompt_for_command,
}
