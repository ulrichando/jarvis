"""Terminal select and multi-select utilities.

Provides interactive single-select and multi-select menus for terminal UIs.
Uses raw tty input with ANSI escape codes for rendering. No external deps.

Provides terminal-based custom select menus.
"""

import os
import sys
import tty
import termios
from typing import Any, Optional


def _get_terminal_size():
    """Get terminal width and height."""
    try:
        cols, rows = os.get_terminal_size()
        return cols, rows
    except OSError:
        return 80, 24


def _read_key(fd: int) -> str:
    """Read a single keypress from the terminal, handling escape sequences."""
    ch = os.read(fd, 1)
    if not ch:
        return ""
    c = ch.decode("utf-8", errors="replace")

    if c == "\x1b":
        # Escape sequence - read more
        ch2 = os.read(fd, 1)
        if not ch2:
            return "escape"
        c2 = ch2.decode("utf-8", errors="replace")
        if c2 == "[":
            ch3 = os.read(fd, 1)
            if not ch3:
                return "escape"
            c3 = ch3.decode("utf-8", errors="replace")
            if c3 == "A":
                return "up"
            elif c3 == "B":
                return "down"
            elif c3 == "C":
                return "right"
            elif c3 == "D":
                return "left"
            elif c3 == "5":
                # Page up: \x1b[5~
                os.read(fd, 1)  # consume '~'
                return "pageup"
            elif c3 == "6":
                # Page down: \x1b[6~
                os.read(fd, 1)  # consume '~'
                return "pagedown"
            return "escape"
        elif c2 == "O":
            ch3 = os.read(fd, 1)
            if not ch3:
                return "escape"
            return "escape"
        return "escape"
    elif c == "\r" or c == "\n":
        return "enter"
    elif c == " ":
        return "space"
    elif c == "\x03":
        # Ctrl+C
        return "ctrl-c"
    elif c == "\x7f" or c == "\x08":
        return "backspace"
    elif c == "\t":
        return "tab"
    else:
        return c


def _hide_cursor():
    sys.stdout.write("\x1b[?25l")
    sys.stdout.flush()


def _show_cursor():
    sys.stdout.write("\x1b[?25h")
    sys.stdout.flush()


def _move_up(n: int):
    if n > 0:
        sys.stdout.write(f"\x1b[{n}A")


def _clear_line():
    sys.stdout.write("\x1b[2K\r")


def _render_select(
    options: list[dict],
    focused_idx: int,
    visible_from: int,
    visible_to: int,
    prompt: str,
    selected_set: Optional[set] = None,
    is_multi: bool = False,
) -> int:
    """Render the select menu and return the number of lines written."""
    lines = []
    total = len(options)

    # Prompt line
    lines.append(f"\x1b[1m{prompt}\x1b[0m")

    # Up arrow indicator
    if visible_from > 0:
        lines.append(f"  \x1b[2m... {visible_from} more above\x1b[0m")

    for i in range(visible_from, min(visible_to, total)):
        opt = options[i]
        label = opt.get("label", str(opt.get("value", "")))
        desc = opt.get("description", "")
        num = i + 1 if i < 9 else ""
        is_focused = i == focused_idx

        # Build the line
        if is_multi:
            checked = selected_set and i in selected_set
            checkbox = "[x]" if checked else "[ ]"
            if is_focused:
                indicator = "\x1b[36m>\x1b[0m"
                line = f" {indicator} {checkbox} "
            else:
                line = f"   {checkbox} "
        else:
            if is_focused:
                indicator = "\x1b[36m>\x1b[0m"
                line = f" {indicator} "
            else:
                line = "   "

        # Number prefix
        if num:
            line += f"\x1b[2m{num}.\x1b[0m "

        # Label with highlight if focused
        if is_focused:
            line += f"\x1b[1;36m{label}\x1b[0m"
        else:
            line += label

        # Description
        if desc:
            line += f"  \x1b[2m{desc}\x1b[0m"

        lines.append(line)

    # Down arrow indicator
    if visible_to < total:
        remaining = total - visible_to
        lines.append(f"  \x1b[2m... {remaining} more below\x1b[0m")

    # Help line
    if is_multi:
        lines.append("\x1b[2m  space: toggle | enter: confirm | esc: cancel\x1b[0m")
    else:
        lines.append("\x1b[2m  enter: select | esc: cancel\x1b[0m")

    output = "\r\n".join(lines)
    sys.stdout.write(output)
    sys.stdout.flush()
    return len(lines)


def _clamp_viewport(
    focused_idx: int,
    visible_from: int,
    visible_to: int,
    visible_count: int,
    total: int,
) -> tuple[int, int]:
    """Adjust viewport to keep focused_idx visible."""
    if focused_idx < visible_from:
        visible_from = focused_idx
        visible_to = min(total, visible_from + visible_count)
    elif focused_idx >= visible_to:
        visible_to = min(total, focused_idx + 1)
        visible_from = max(0, visible_to - visible_count)
    return visible_from, visible_to


def select(
    options: list[dict],
    prompt: str = "Select an option:",
    default: Any = None,
    visible_count: int = 10,
) -> Optional[Any]:
    """Interactive single-select menu.

    Args:
        options: List of dicts with keys: label (str), value (any), description (str, optional).
        prompt: Text displayed above the options.
        default: Default value to pre-focus.
        visible_count: Number of options visible at once.

    Returns:
        The selected option's value, or None if cancelled.

    Controls:
        Arrow keys: navigate
        Enter: select
        Esc: cancel
        1-9: jump to option by number
    """
    if not options:
        return None

    total = len(options)
    visible_count = min(visible_count, total)

    # Find default index
    focused_idx = 0
    if default is not None:
        for i, opt in enumerate(options):
            if opt.get("value") == default:
                focused_idx = i
                break

    # Initial viewport
    visible_from = 0
    visible_to = visible_count
    if focused_idx >= visible_count:
        visible_to = min(total, focused_idx + 1)
        visible_from = max(0, visible_to - visible_count)

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    lines_drawn = 0

    try:
        tty.setcbreak(fd)
        _hide_cursor()

        while True:
            # Clear previous output
            if lines_drawn > 0:
                _move_up(lines_drawn - 1)
                for _ in range(lines_drawn):
                    _clear_line()
                    sys.stdout.write("\x1b[1B")
                _move_up(lines_drawn)

            lines_drawn = _render_select(
                options, focused_idx, visible_from, visible_to, prompt
            )

            key = _read_key(fd)

            if key == "up":
                if focused_idx > 0:
                    focused_idx -= 1
                else:
                    # Wrap to last
                    focused_idx = total - 1
                    visible_to = total
                    visible_from = max(0, visible_to - visible_count)
                visible_from, visible_to = _clamp_viewport(
                    focused_idx, visible_from, visible_to, visible_count, total
                )

            elif key == "down":
                if focused_idx < total - 1:
                    focused_idx += 1
                else:
                    # Wrap to first
                    focused_idx = 0
                    visible_from = 0
                    visible_to = min(total, visible_count)
                visible_from, visible_to = _clamp_viewport(
                    focused_idx, visible_from, visible_to, visible_count, total
                )

            elif key == "pageup":
                focused_idx = max(0, focused_idx - visible_count)
                visible_from, visible_to = _clamp_viewport(
                    focused_idx, visible_from, visible_to, visible_count, total
                )

            elif key == "pagedown":
                focused_idx = min(total - 1, focused_idx + visible_count)
                visible_from, visible_to = _clamp_viewport(
                    focused_idx, visible_from, visible_to, visible_count, total
                )

            elif key == "enter":
                opt = options[focused_idx]
                if not opt.get("disabled", False):
                    return opt.get("value")

            elif key == "escape":
                return None

            elif key == "ctrl-c":
                return None

            elif key.isdigit() and key != "0":
                idx = int(key) - 1
                if 0 <= idx < total:
                    opt = options[idx]
                    if not opt.get("disabled", False):
                        return opt.get("value")

    finally:
        _show_cursor()
        sys.stdout.write("\r\n")
        sys.stdout.flush()
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def select_multi(
    options: list[dict],
    prompt: str = "Select options:",
    defaults: Optional[list] = None,
    visible_count: int = 10,
) -> Optional[list]:
    """Interactive multi-select menu.

    Args:
        options: List of dicts with keys: label (str), value (any), description (str, optional).
        prompt: Text displayed above the options.
        defaults: List of values that are pre-selected.
        visible_count: Number of options visible at once.

    Returns:
        List of selected values, or None if cancelled.

    Controls:
        Arrow keys: navigate
        Space: toggle selection
        Enter: confirm selection
        Esc: cancel
        1-9: toggle option by number
    """
    if not options:
        return []

    total = len(options)
    visible_count = min(visible_count, total)

    # Build selected set (indices)
    selected_indices: set[int] = set()
    if defaults:
        for i, opt in enumerate(options):
            if opt.get("value") in defaults:
                selected_indices.add(i)

    focused_idx = 0
    visible_from = 0
    visible_to = min(total, visible_count)

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    lines_drawn = 0

    try:
        tty.setcbreak(fd)
        _hide_cursor()

        while True:
            # Clear previous output
            if lines_drawn > 0:
                _move_up(lines_drawn - 1)
                for _ in range(lines_drawn):
                    _clear_line()
                    sys.stdout.write("\x1b[1B")
                _move_up(lines_drawn)

            lines_drawn = _render_select(
                options,
                focused_idx,
                visible_from,
                visible_to,
                prompt,
                selected_set=selected_indices,
                is_multi=True,
            )

            key = _read_key(fd)

            if key == "up":
                if focused_idx > 0:
                    focused_idx -= 1
                else:
                    focused_idx = total - 1
                    visible_to = total
                    visible_from = max(0, visible_to - visible_count)
                visible_from, visible_to = _clamp_viewport(
                    focused_idx, visible_from, visible_to, visible_count, total
                )

            elif key == "down":
                if focused_idx < total - 1:
                    focused_idx += 1
                else:
                    focused_idx = 0
                    visible_from = 0
                    visible_to = min(total, visible_count)
                visible_from, visible_to = _clamp_viewport(
                    focused_idx, visible_from, visible_to, visible_count, total
                )

            elif key == "pageup":
                focused_idx = max(0, focused_idx - visible_count)
                visible_from, visible_to = _clamp_viewport(
                    focused_idx, visible_from, visible_to, visible_count, total
                )

            elif key == "pagedown":
                focused_idx = min(total - 1, focused_idx + visible_count)
                visible_from, visible_to = _clamp_viewport(
                    focused_idx, visible_from, visible_to, visible_count, total
                )

            elif key == "space":
                opt = options[focused_idx]
                if not opt.get("disabled", False):
                    if focused_idx in selected_indices:
                        selected_indices.discard(focused_idx)
                    else:
                        selected_indices.add(focused_idx)

            elif key == "enter":
                result = []
                for i in sorted(selected_indices):
                    result.append(options[i].get("value"))
                return result

            elif key == "escape":
                return None

            elif key == "ctrl-c":
                return None

            elif key.isdigit() and key != "0":
                idx = int(key) - 1
                if 0 <= idx < total:
                    opt = options[idx]
                    if not opt.get("disabled", False):
                        if idx in selected_indices:
                            selected_indices.discard(idx)
                        else:
                            selected_indices.add(idx)

    finally:
        _show_cursor()
        sys.stdout.write("\r\n")
        sys.stdout.flush()
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
