"""Render placeholder text with optional cursor display."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional


@dataclass
class PlaceholderResult:
    rendered_placeholder: Optional[str] = None
    show_placeholder: bool = False


def render_placeholder(
    value: str,
    terminal_focus: bool = True,
    placeholder: Optional[str] = None,
    show_cursor: bool = False,
    focus: bool = False,
    invert: Optional[Callable[[str], str]] = None,
    hide_placeholder_text: bool = False,
) -> PlaceholderResult:
    """Render placeholder text with optional cursor indication.

    Args:
        value: Current input value.
        terminal_focus: Whether the terminal has focus.
        placeholder: Placeholder text to display.
        show_cursor: Whether to show a cursor indicator.
        focus: Whether the input has focus.
        invert: Function to invert text (e.g., for cursor display).
        hide_placeholder_text: If True, show only cursor, no text.

    Returns:
        PlaceholderResult with rendered text and visibility flag.
    """
    if invert is None:
        # Default invert wraps text in ANSI inverse
        def invert(text: str) -> str:
            return f"\033[7m{text}\033[27m"

    rendered_placeholder: Optional[str] = None

    if placeholder is not None:
        if hide_placeholder_text:
            # Voice recording: show only the cursor, no placeholder text
            rendered_placeholder = invert(" ") if (show_cursor and focus and terminal_focus) else ""
        else:
            # Dim the placeholder text
            rendered_placeholder = f"\033[2m{placeholder}\033[22m"

            # Show inverse cursor only when both input and terminal are focused
            if show_cursor and focus and terminal_focus:
                if len(placeholder) > 0:
                    rendered_placeholder = (
                        invert(placeholder[0])
                        + f"\033[2m{placeholder[1:]}\033[22m"
                    )
                else:
                    rendered_placeholder = invert(" ")

    show_placeholder = len(value) == 0 and placeholder is not None

    return PlaceholderResult(
        rendered_placeholder=rendered_placeholder,
        show_placeholder=show_placeholder,
    )
