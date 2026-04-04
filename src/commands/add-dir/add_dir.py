"""Add-dir command implementation."""

from __future__ import annotations

from typing import Any


async def call(on_done: Any = None, context: Any = None, args: str = "", **_kwargs: Any) -> None:
    """Add a directory to the workspace."""
    from .validation import validate_directory_for_workspace, add_dir_help_message

    if not context:
        if on_done:
            on_done("No context provided.")
        return

    result = await validate_directory_for_workspace(args)
    message = add_dir_help_message(result)
    if on_done:
        on_done(message)
