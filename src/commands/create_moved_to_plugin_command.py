"""Factory for commands that have been moved to plugins."""

from __future__ import annotations

import os
from typing import Any, Callable, Awaitable


def create_moved_to_plugin_command(
    *,
    name: str,
    description: str,
    progress_message: str,
    plugin_name: str,
    plugin_command: str,
    get_prompt_while_marketplace_is_private: Callable[..., Awaitable[list[dict[str, str]]]],
) -> dict[str, Any]:
    """Create a command definition for a command that has moved to a plugin."""

    async def get_prompt_for_command(args: str = "", context: Any = None, **_kwargs: Any) -> list[dict[str, str]]:
        if os.environ.get("USER_TYPE") == "ant":
            return [
                {
                    "type": "text",
                    "text": (
                        f"This command has been moved to a plugin. Tell the user:\n\n"
                        f"1. To install the plugin, run:\n"
                        f"   claude plugin install {plugin_name}@claude-code-marketplace\n\n"
                        f"2. After installation, use /{plugin_name}:{plugin_command} to run this command\n\n"
                        f"3. For more information, see: "
                        f"https://github.com/anthropics/claude-code-marketplace/blob/main/{plugin_name}/README.md\n\n"
                        f"Do not attempt to run the command. Simply inform the user about the plugin installation."
                    ),
                }
            ]
        return await get_prompt_while_marketplace_is_private(args, context)

    return {
        "type": "prompt",
        "name": name,
        "description": description,
        "progress_message": progress_message,
        "content_length": 0,
        "user_facing_name": lambda: name,
        "source": "builtin",
        "get_prompt_for_command": get_prompt_for_command,
    }
