"""Cached line width calculation.

During streaming, text grows but completed lines are immutable.
Caching string_width per-line avoids re-measuring hundreds of
unchanged lines on every token.
"""

from .string_width import string_width

_cache: dict[str, int] = {}
_MAX_CACHE_SIZE = 4096


def line_width(line: str) -> int:
    """Get the display width of a line, with caching."""
    cached = _cache.get(line)
    if cached is not None:
        return cached

    width = string_width(line)

    if len(_cache) >= _MAX_CACHE_SIZE:
        _cache.clear()

    _cache[line] = width
    return width
