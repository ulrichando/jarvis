"""Message formatting utilities for JARVIS terminal display.

Provides helpers to render tool calls, tool results, chat messages,
errors, and truncated output with ANSI colors. Handles AssistantToolUseMessage,
CollapsedReadSearchContent, and SystemAPIErrorMessage components.
"""

from __future__ import annotations

import json
import textwrap
from datetime import datetime
from typing import Optional

# ── ANSI codes (matching src/cli/display.py) ─────────────────────────
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
ITALIC = "\033[3m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
MAGENTA = "\033[35m"
CYAN = "\033[36m"
GREY = "\033[90m"

# Icons by tool name (subset -- callers can extend)
_TOOL_ICONS = {
    "bash": ">>>",
    "read_file": "[R]",
    "write_file": "[W]",
    "edit_file": "[E]",
    "search_files": "[S]",
    "web_search": "[web]",
    "web_fetch": "[web]",
    "think": "[T]",
    "dispatch": "[D]",
}

_ROLE_STYLE = {
    "user": (BLUE, "user"),
    "assistant": (MAGENTA, "assistant"),
    "system": (YELLOW, "system"),
    "tool": (CYAN, "tool"),
}

# ── Public API ───────────────────────────────────────────────────────


def format_tool_call(name: str, args: dict, *, max_arg_len: int = 80) -> str:
    """Format a tool call for display: icon + name + truncated args.

    Example output (colored)::

        >>> bash  command="ls -la /tmp"
        [R] read_file  file_path="src/main.py"
    """
    icon = _TOOL_ICONS.get(name, "[?]")
    arg_parts: list[str] = []

    for key, val in args.items():
        if isinstance(val, str):
            display = val
        else:
            try:
                display = json.dumps(val, ensure_ascii=False)
            except (TypeError, ValueError):
                display = str(val)

        if len(display) > max_arg_len:
            display = display[:max_arg_len - 3] + "..."
        arg_parts.append(f"{key}={_quote(display)}")

    args_str = "  " + " ".join(arg_parts) if arg_parts else ""
    return f"{CYAN}{icon}{RESET} {BOLD}{name}{RESET}{DIM}{args_str}{RESET}"


def format_tool_result(
    name: str,
    result: str,
    success: bool = True,
    elapsed: float = 0.0,
    *,
    max_lines: int = 20,
) -> str:
    """Format a tool result with status icon + timing.

    Successful results get a green check, failures a red cross.
    The result body is truncated via :func:`truncate_output`.
    """
    icon = f"{GREEN}+{RESET}" if success else f"{RED}x{RESET}"
    timing = ""
    if elapsed > 0:
        if elapsed < 1.0:
            timing = f" {DIM}({elapsed * 1000:.0f}ms){RESET}"
        else:
            timing = f" {DIM}({elapsed:.1f}s){RESET}"

    header = f"{icon} {BOLD}{name}{RESET}{timing}"
    body = truncate_output(result, max_lines=max_lines)
    if body:
        # Indent the body under the header
        indented = textwrap.indent(body, "  ")
        return f"{header}\n{indented}"
    return header


def format_message(
    role: str,
    content: str,
    timestamp: Optional[datetime] = None,
) -> str:
    """Format a chat message with a colored role prefix.

    Example::

        [assistant] Hello, how can I help?
        [user] 14:32  Show me the diff
    """
    color, label = _ROLE_STYLE.get(role, (GREY, role))
    ts = ""
    if timestamp is not None:
        ts = f" {DIM}{timestamp.strftime('%H:%M')}{RESET}"

    prefix = f"{color}[{label}]{RESET}{ts}"
    return f"{prefix} {content}"


def truncate_output(text: str, max_lines: int = 20) -> str:
    """Smart truncation keeping first and last lines visible.

    If the text exceeds *max_lines*, the middle is replaced with a
    ``... N lines hidden ...`` indicator.  Returns the text unchanged
    when it fits within the limit.
    """
    if not text:
        return text

    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text

    # Keep roughly equal head/tail with the indicator in the middle
    keep_head = max_lines // 2
    keep_tail = max_lines - keep_head - 1  # -1 for the indicator
    hidden = len(lines) - keep_head - keep_tail

    head = lines[:keep_head]
    tail = lines[-keep_tail:] if keep_tail > 0 else []
    indicator = f"{DIM}... {hidden} lines hidden ...{RESET}"

    return "\n".join(head + [indicator] + tail)


def format_error(error: str) -> str:
    """Format an error message with icon and red coloring.

    Example::

        x Error: command not found: foobar
    """
    return f"{RED}{BOLD}x Error:{RESET} {RED}{error}{RESET}"


# ── Internal helpers ─────────────────────────────────────────────────

def _quote(s: str) -> str:
    """Wrap a string in quotes, using double quotes."""
    # Escape embedded double-quotes
    return '"' + s.replace('"', '\\"') + '"'
