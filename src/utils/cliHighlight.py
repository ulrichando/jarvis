"""CLI syntax highlighting utilities."""

from __future__ import annotations

import os
from typing import Optional

_pygments_loaded = False
_highlight_func = None


def _load_pygments():
    global _pygments_loaded, _highlight_func
    if _pygments_loaded:
        return
    _pygments_loaded = True
    try:
        from pygments import highlight
        from pygments.formatters import TerminalFormatter

        _highlight_func = lambda code, lexer: highlight(code, lexer, TerminalFormatter())
    except ImportError:
        _highlight_func = None


async def get_language_name(file_path: str) -> str:
    """Get the language name for a file path (e.g., 'foo/bar.ts' -> 'TypeScript')."""
    _load_pygments()
    ext = os.path.splitext(file_path)[1].lstrip(".")
    if not ext:
        return "unknown"

    try:
        from pygments.lexers import get_lexer_by_name

        lexer = get_lexer_by_name(ext)
        return lexer.name
    except Exception:
        return "unknown"
