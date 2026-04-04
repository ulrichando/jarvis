"""Prompts from Claude in Chrome notification."""

from __future__ import annotations

from typing import Callable, Optional


def check_prompts_from_chrome(
    add_notification: Optional[Callable] = None,
    pending_prompts: Optional[list] = None,
) -> None:
    """Show notification about pending prompts from Chrome extension.

    Equivalent to usePromptsFromClaudeInChrome React hook.
    """
    if not pending_prompts or not add_notification:
        return
    count = len(pending_prompts)
    add_notification(
        key="chrome-prompts",
        text=f"{count} prompt{'s' if count != 1 else ''} from Chrome",
        priority="immediate",
    )
