"""
Terminal hyperlink utilities using OSC 8 escape sequences.
"""

from __future__ import annotations

from typing import Optional

# OSC 8 hyperlink escape sequences
OSC8_START = "\x1b]8;;"
OSC8_END = "\x07"


def create_hyperlink(
    url: str,
    content: Optional[str] = None,
    supports_hyperlinks: bool = True,
) -> str:
    """
    Create a clickable hyperlink using OSC 8 escape sequences.
    Falls back to plain text if the terminal doesn't support hyperlinks.

    Args:
        url: The URL to link to.
        content: Optional content to display as the link text.
        supports_hyperlinks: Whether the terminal supports hyperlinks.
    """
    if not supports_hyperlinks:
        return url

    display_text = content or url
    # ANSI blue color
    colored_text = f"\x1b[34m{display_text}\x1b[0m"
    return f"{OSC8_START}{url}{OSC8_END}{colored_text}{OSC8_START}{OSC8_END}"
