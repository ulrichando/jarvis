"""Terminal formatting utilities: colors, progress bars, status icons, boxes.

Provides ANSI escape code helpers for rich terminal output.
Provides JARVIS design-system formatting for terminal output.
"""

import os
import shutil
from typing import Optional


# ---------------------------------------------------------------------------
# ANSI Color constants (256-color mode)
# ---------------------------------------------------------------------------

class Color:
    """ANSI 256-color and style escape sequences."""

    RESET = "\x1b[0m"
    BOLD = "\x1b[1m"
    DIM = "\x1b[2m"
    ITALIC = "\x1b[3m"
    UNDERLINE = "\x1b[4m"
    BLINK = "\x1b[5m"
    INVERSE = "\x1b[7m"
    STRIKETHROUGH = "\x1b[9m"

    # Standard colors (foreground)
    BLACK = "\x1b[30m"
    RED = "\x1b[31m"
    GREEN = "\x1b[32m"
    YELLOW = "\x1b[33m"
    BLUE = "\x1b[34m"
    MAGENTA = "\x1b[35m"
    CYAN = "\x1b[36m"
    WHITE = "\x1b[37m"

    # Bright colors (foreground)
    BRIGHT_BLACK = "\x1b[90m"
    BRIGHT_RED = "\x1b[91m"
    BRIGHT_GREEN = "\x1b[92m"
    BRIGHT_YELLOW = "\x1b[93m"
    BRIGHT_BLUE = "\x1b[94m"
    BRIGHT_MAGENTA = "\x1b[95m"
    BRIGHT_CYAN = "\x1b[96m"
    BRIGHT_WHITE = "\x1b[97m"

    # Standard colors (background)
    BG_BLACK = "\x1b[40m"
    BG_RED = "\x1b[41m"
    BG_GREEN = "\x1b[42m"
    BG_YELLOW = "\x1b[43m"
    BG_BLUE = "\x1b[44m"
    BG_MAGENTA = "\x1b[45m"
    BG_CYAN = "\x1b[46m"
    BG_WHITE = "\x1b[47m"

    # Semantic aliases (matching JARVIS theme system)
    SUCCESS = "\x1b[32m"       # green
    ERROR = "\x1b[31m"         # red
    WARNING = "\x1b[33m"       # yellow
    INFO = "\x1b[34m"          # blue
    SUGGESTION = "\x1b[36m"    # cyan
    SECONDARY = "\x1b[90m"     # dim/gray
    ACCENT = "\x1b[35m"        # magenta
    PRIMARY = "\x1b[97m"       # bright white

    @staticmethod
    def fg256(n: int) -> str:
        """Foreground color from 256-color palette (0-255)."""
        return f"\x1b[38;5;{n}m"

    @staticmethod
    def bg256(n: int) -> str:
        """Background color from 256-color palette (0-255)."""
        return f"\x1b[48;5;{n}m"

    @staticmethod
    def rgb(r: int, g: int, b: int) -> str:
        """Foreground color from 24-bit RGB."""
        return f"\x1b[38;2;{r};{g};{b}m"

    @staticmethod
    def bg_rgb(r: int, g: int, b: int) -> str:
        """Background color from 24-bit RGB."""
        return f"\x1b[48;2;{r};{g};{b}m"


def colorize(text: str, color_code: str) -> str:
    """Apply an ANSI color code to text, resetting at the end."""
    return f"{color_code}{text}{Color.RESET}"


# ---------------------------------------------------------------------------
# Progress bar
# ---------------------------------------------------------------------------

# Sub-block characters for smooth progress rendering
_BLOCKS = [" ", "\u258f", "\u258e", "\u258d", "\u258c", "\u258b", "\u258a", "\u2589", "\u2588"]


def progress_bar(
    current: int | float,
    total: int | float,
    width: int = 30,
    fill_color: str = Color.CYAN,
    empty_color: str = Color.DIM,
) -> str:
    """Render a unicode progress bar string with percentage.

    Args:
        current: Current progress value.
        total: Maximum progress value.
        width: Character width of the bar (default 30).
        fill_color: ANSI color for filled portion.
        empty_color: ANSI color for empty portion.

    Returns:
        Formatted string like: ``████████░░░░ 67%``

    Example::

        >>> progress_bar(67, 100)
        '████████████████████░░░░░░░░░░ 67%'
    """
    if total <= 0:
        ratio = 0.0
    else:
        ratio = min(1.0, max(0.0, current / total))

    whole = int(ratio * width)
    segments = [_BLOCKS[-1] * whole]

    if whole < width:
        remainder = ratio * width - whole
        middle_idx = int(remainder * len(_BLOCKS))
        middle_idx = min(middle_idx, len(_BLOCKS) - 1)
        segments.append(_BLOCKS[middle_idx])
        empty = width - whole - 1
        if empty > 0:
            segments.append(_BLOCKS[0] * empty)

    bar = "".join(segments)
    pct = int(ratio * 100)

    filled_part = bar[:whole]
    rest_part = bar[whole:]

    return f"{fill_color}{filled_part}{empty_color}{rest_part}{Color.RESET} {pct}%"


# ---------------------------------------------------------------------------
# Status icons
# ---------------------------------------------------------------------------

_STATUS_ICONS = {
    "success":   ("\u2714", Color.GREEN),       # checkmark
    "error":     ("\u2718", Color.RED),          # cross
    "warning":   ("\u26a0", Color.YELLOW),       # warning triangle
    "info":      ("\u2139", Color.CYAN),         # info circle
    "pending":   ("\u25cb", Color.DIM),          # open circle
    "running":   ("\u2026", Color.DIM),          # ellipsis
    "cancelled": ("\u2205", Color.DIM),          # empty set
    "loading":   ("\u2026", Color.DIM),          # ellipsis (alias)
}


def status_icon(status: str) -> str:
    """Return a colored unicode icon for the given status.

    Supported statuses: success, error, warning, info, pending, running,
    cancelled, loading.

    Args:
        status: One of the supported status strings.

    Returns:
        A colored unicode character, or ``?`` for unknown statuses.
    """
    entry = _STATUS_ICONS.get(status)
    if entry is None:
        return "?"
    icon, color = entry
    return f"{color}{icon}{Color.RESET}"


# ---------------------------------------------------------------------------
# Divider
# ---------------------------------------------------------------------------

def divider(width: Optional[int] = None, char: str = "\u2500") -> str:
    """Return a horizontal divider line.

    Args:
        width: Character width. Defaults to terminal width.
        char: The character to repeat (default: box-drawing horizontal).

    Returns:
        A string of repeated characters spanning the given width.
    """
    if width is None:
        try:
            width = shutil.get_terminal_size().columns
        except Exception:
            width = 80
    return f"{Color.DIM}{char * width}{Color.RESET}"


# ---------------------------------------------------------------------------
# Box
# ---------------------------------------------------------------------------

def _visible_len(text: str) -> int:
    """Length of text excluding ANSI escape sequences."""
    import re
    return len(re.sub(r"\x1b\[[0-9;]*m", "", text))


def box(text: str, title: Optional[str] = None, width: Optional[int] = None) -> str:
    """Wrap text in a unicode box border.

    Args:
        text: The content to box. May contain multiple lines.
        title: Optional title to display on the top border.
        width: Box width. Defaults to longest line + 4, capped at terminal width.

    Returns:
        Multi-line string with box-drawing characters around the text.

    Example::

        >>> print(box("Hello, world!", title="Greeting"))
        +-- Greeting --------+
        | Hello, world!      |
        +--------------------+
    """
    lines = text.split("\n")
    max_content = max((_visible_len(line) for line in lines), default=0)

    if width is None:
        try:
            term_width = shutil.get_terminal_size().columns
        except Exception:
            term_width = 80
        width = min(term_width, max_content + 4)

    inner_width = width - 2  # for left and right border chars

    # Top border
    if title:
        title_str = f" {title} "
        remaining = inner_width - len(title_str)
        if remaining < 0:
            remaining = 0
        top = f"\u250c{title_str}{''.join(['\u2500'] * remaining)}\u2510"
    else:
        top = f"\u250c{'\u2500' * inner_width}\u2510"

    # Content lines
    result_lines = [top]
    for line in lines:
        vis_len = _visible_len(line)
        padding = inner_width - vis_len
        if padding < 0:
            padding = 0
        result_lines.append(f"\u2502{line}{' ' * padding}\u2502")

    # Bottom border
    bottom = f"\u2514{'\u2500' * inner_width}\u2518"
    result_lines.append(bottom)

    return "\n".join(result_lines)
