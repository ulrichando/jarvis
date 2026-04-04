"""Find the widest line in a multi-line string."""

from .line_width_cache import line_width


def widest_line(string: str) -> int:
    """Return the display width of the widest line in the string."""
    max_width = 0
    start = 0

    while start <= len(string):
        end = string.find("\n", start)
        line = string[start:] if end == -1 else string[start:end]
        max_width = max(max_width, line_width(line))
        if end == -1:
            break
        start = end + 1

    return max_width
