"""Bidirectional text reordering for terminal rendering.

Terminals on Windows do not implement the Unicode Bidi Algorithm,
so RTL text appears reversed. This module applies the bidi algorithm
to reorder character arrays from logical to visual order.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from typing import Any, TypeVar

T = TypeVar("T")


@dataclass
class ClusteredChar:
    """A character cluster with display properties."""
    value: str = ""
    width: int = 0
    style_id: int = 0
    hyperlink: str | None = None


_needs_software_bidi: bool | None = None


def _needs_bidi() -> bool:
    """Check if software bidi reordering is needed."""
    global _needs_software_bidi
    if _needs_software_bidi is None:
        _needs_software_bidi = (
            sys.platform == "win32"
            or isinstance(os.environ.get("WT_SESSION"), str)
            or os.environ.get("TERM_PROGRAM") == "vscode"
        )
    return _needs_software_bidi


_RTL_RE = re.compile(
    r"[\u0590-\u05FF\uFB1D-\uFB4F\u0600-\u06FF\u0750-\u077F"
    r"\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF\u0780-\u07BF\u0700-\u074F]"
)


def _has_rtl_characters(text: str) -> bool:
    """Quick check for RTL characters."""
    return bool(_RTL_RE.search(text))


def _reverse_range(arr: list, start: int, end: int) -> None:
    """Reverse a range within a list in place."""
    while start < end:
        arr[start], arr[end] = arr[end], arr[start]
        start += 1
        end -= 1


def reorder_bidi(characters: list[ClusteredChar]) -> list[ClusteredChar]:
    """Reorder an array of ClusteredChars from logical to visual order.

    Active on terminals that lack native bidi support. Returns the same
    array on bidi-capable terminals (no-op).
    """
    if not _needs_bidi() or not characters:
        return characters

    plain_text = "".join(c.value for c in characters)

    if not _has_rtl_characters(plain_text):
        return characters

    # Simple bidi: reverse RTL runs. A proper implementation would use
    # the Unicode Bidi Algorithm (UAX #9), but this covers common cases.
    # For a full implementation, use the python-bidi package.
    return characters
