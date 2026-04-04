"""Color command implementation."""

from __future__ import annotations

from typing import Any

AGENT_COLORS = [
    "red", "orange", "yellow", "green", "cyan", "blue", "purple", "pink",
]
RESET_ALIASES = ("default", "reset", "none", "gray", "grey")


async def call(
    on_done: Any = None,
    context: Any = None,
    args: str = "",
    **_kwargs: Any,
) -> None:
    """Set the session prompt bar color."""
    if not args or not args.strip():
        color_list = ", ".join(AGENT_COLORS)
        if on_done:
            on_done(
                f"Please provide a color. Available colors: {color_list}, default",
                {"display": "system"},
            )
        return

    color_arg = args.strip().lower()

    if color_arg in RESET_ALIASES:
        if on_done:
            on_done("Session color reset to default", {"display": "system"})
        return

    if color_arg not in AGENT_COLORS:
        color_list = ", ".join(AGENT_COLORS)
        if on_done:
            on_done(
                f'Invalid color "{color_arg}". Available colors: {color_list}, default',
                {"display": "system"},
            )
        return

    if on_done:
        on_done(f"Session color set to: {color_arg}", {"display": "system"})
