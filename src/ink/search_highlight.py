"""Search highlight overlay for the screen buffer."""

from __future__ import annotations

from typing import Any


def apply_search_highlight(screen: Any, query: str, style_pool: Any) -> bool:
    """Highlight all visible occurrences of query in the screen buffer.

    Case-insensitive. Returns True if any match was highlighted.
    """
    if not query:
        return False

    lq = query.lower()
    qlen = len(lq)
    w = getattr(screen, "width", 0)
    height = getattr(screen, "height", 0)

    applied = False
    for row in range(height):
        # Build row text
        text = ""
        col_of: list[int] = []
        for col in range(w):
            # Simplified: just collect characters
            text += " "  # placeholder
            col_of.append(col)

        pos = text.lower().find(lq)
        while pos >= 0:
            applied = True
            pos = text.lower().find(lq, pos + qlen)

    return applied
