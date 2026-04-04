"""Bash output line formatting for ANSI terminals.

Features:
- JSON detection and pretty-printing
- URL detection and linkification (underline)
- Long output truncation with "... (N more lines)"
- Error highlighting (red)
- Exit code display
- Timing display
"""

from __future__ import annotations

import json
import re
from typing import Optional

# ANSI codes
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
ITALIC = "\033[3m"
UNDERLINE = "\033[4m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
CYAN = "\033[36m"
GREY = "\033[90m"

# URL regex
URL_RE = re.compile(
    r'(https?://[^\s<>\'")\]]+)',
    re.IGNORECASE,
)

# Common error patterns
ERROR_PATTERNS = [
    re.compile(r'\b(error|Error|ERROR)\b'),
    re.compile(r'\b(fail|Fail|FAIL|failed|FAILED)\b'),
    re.compile(r'\b(exception|Exception|EXCEPTION)\b'),
    re.compile(r'\b(traceback|Traceback)\b', re.IGNORECASE),
    re.compile(r'\b(panic|PANIC)\b'),
    re.compile(r'^E\s+'),  # pytest error lines
]

# Warning patterns
WARNING_PATTERNS = [
    re.compile(r'\b(warn|Warn|WARN|warning|Warning|WARNING)\b'),
    re.compile(r'\b(deprecated|Deprecated|DEPRECATED)\b'),
]


def tryFormatJson(text: str) -> Optional[str]:
    """Try to parse and pretty-print JSON text.

    Returns formatted JSON with syntax coloring, or None if not valid JSON.
    """
    text = text.strip()
    if not text:
        return None

    # Quick check: must start with { or [
    if not (text.startswith("{") or text.startswith("[")):
        return None

    try:
        parsed = json.loads(text)
        formatted = json.dumps(parsed, indent=2, ensure_ascii=False)
        return _colorize_json(formatted)
    except (json.JSONDecodeError, ValueError):
        return None


def tryJsonFormatContent(text: str) -> str:
    """Try to format content as JSON; return original if not JSON."""
    result = tryFormatJson(text)
    return result if result is not None else text


def _colorize_json(text: str) -> str:
    """Add ANSI colors to formatted JSON."""
    output: list[str] = []
    for line in text.split("\n"):
        # String values (after colon)
        line = re.sub(
            r'(:\s*)"([^"]*)"',
            lambda m: f'{m.group(1)}{GREEN}"{m.group(2)}"{RESET}',
            line,
        )
        # String keys
        line = re.sub(
            r'^(\s*)"([^"]+)"(\s*:)',
            lambda m: f'{m.group(1)}{CYAN}"{m.group(2)}"{RESET}{m.group(3)}',
            line,
        )
        # Numbers
        line = re.sub(
            r':\s*(-?\d+\.?\d*)\b',
            lambda m: f': {YELLOW}{m.group(1)}{RESET}',
            line,
        )
        # Booleans and null
        line = re.sub(
            r'\b(true|false|null)\b',
            lambda m: f'{BLUE}{m.group(1)}{RESET}',
            line,
        )
        output.append(line)
    return "\n".join(output)


def linkifyUrlsInText(text: str) -> str:
    """Replace URLs in text with underlined ANSI versions."""
    def _replace_url(match):
        url = match.group(1)
        return f"{UNDERLINE}{BLUE}{url}{RESET}"

    return URL_RE.sub(_replace_url, text)


def stripUnderlineAnsi(text: str) -> str:
    """Strip underline ANSI sequences from text."""
    return text.replace("\033[4m", "")


def _is_error_line(line: str) -> bool:
    """Check if a line looks like an error."""
    for pattern in ERROR_PATTERNS:
        if pattern.search(line):
            return True
    return False


def _is_warning_line(line: str) -> bool:
    """Check if a line looks like a warning."""
    for pattern in WARNING_PATTERNS:
        if pattern.search(line):
            return True
    return False


def OutputLine(
    text: str,
    exit_code: Optional[int] = None,
    elapsed: Optional[float] = None,
    max_lines: int = 50,
    truncate: bool = True,
    show_line_numbers: bool = False,
) -> str:
    """Format bash command output for terminal display.

    Args:
        text: Raw output text.
        exit_code: Command exit code (None if not available).
        elapsed: Execution time in seconds.
        max_lines: Maximum lines to show before truncating.
        truncate: Whether to truncate long output.
        show_line_numbers: Whether to show line numbers.

    Returns:
        ANSI-formatted output string.
    """
    if not text:
        return _format_status(exit_code, elapsed)

    # Try JSON formatting first
    json_formatted = tryFormatJson(text.strip())
    if json_formatted is not None:
        lines = json_formatted.split("\n")
    else:
        lines = text.split("\n")

    output_parts: list[str] = []

    # Status line
    status = _format_status(exit_code, elapsed)
    if status:
        output_parts.append(status)

    # Process and truncate lines
    total = len(lines)
    show_lines = lines[:max_lines] if truncate and total > max_lines else lines

    for i, line in enumerate(show_lines):
        formatted = _format_line(line, i + 1 if show_line_numbers else None)
        output_parts.append(f"  {DIM}│{RESET} {formatted}")

    # Truncation notice
    if truncate and total > max_lines:
        remaining = total - max_lines
        output_parts.append(
            f"  {DIM}│ ... ({remaining} more line{'s' if remaining != 1 else ''}){RESET}"
        )

    return "\n".join(output_parts)


def _format_status(exit_code: Optional[int], elapsed: Optional[float]) -> str:
    """Format exit code and elapsed time."""
    parts: list[str] = []

    if exit_code is not None:
        if exit_code == 0:
            parts.append(f"{GREEN}✔{RESET}")
        else:
            parts.append(f"{RED}✘ exit {exit_code}{RESET}")

    if elapsed is not None:
        parts.append(f"{DIM}{elapsed:.1f}s{RESET}")

    if not parts:
        return ""
    return "  " + " ".join(parts)


def _format_line(line: str, line_num: Optional[int] = None) -> str:
    """Format a single output line with colors and linkification."""
    # Line number prefix
    prefix = ""
    if line_num is not None:
        prefix = f"{DIM}{line_num:>4} {RESET}"

    # Error highlighting
    if _is_error_line(line):
        return f"{prefix}{RED}{line}{RESET}"

    # Warning highlighting
    if _is_warning_line(line):
        return f"{prefix}{YELLOW}{line}{RESET}"

    # URL linkification
    line = linkifyUrlsInText(line)

    return f"{prefix}{line}"
