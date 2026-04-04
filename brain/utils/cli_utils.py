"""JARVIS CLI Utilities -- helpers for terminal I/O and output formatting."""

import sys
import os
import json
import re
import logging
from typing import NoReturn

log = logging.getLogger(__name__)


# -- Exit Helpers --

def cli_error(message: str = "") -> NoReturn:
    """Print error to stderr and exit with code 1."""
    if message:
        sys.stderr.write(f"Error: {message}\n")
    sys.exit(1)

def cli_ok(message: str = "") -> NoReturn:
    """Print message to stdout and exit with code 0."""
    if message:
        sys.stdout.write(message)
        if not message.endswith("\n"):
            sys.stdout.write("\n")
        sys.stdout.flush()
    sys.exit(0)


# -- NDJSON Safe Output --

_UNICODE_LINE_SEPARATORS = re.compile('[\u2028\u2029]')

def ndjson_safe_stringify(obj: dict | list) -> str:
    """JSON stringify safe for line-delimited transports.

    Escapes Unicode line/paragraph separators (U+2028, U+2029) which are
    valid JSON but act as line terminators in JavaScript, breaking NDJSON.
    """
    raw = json.dumps(obj, ensure_ascii=False)
    return _UNICODE_LINE_SEPARATORS.sub(
        lambda m: f"\\u{ord(m.group()):04x}", raw
    )

def write_ndjson(obj: dict | list, stream=None):
    """Write a single NDJSON line to stream (default stdout)."""
    if stream is None:
        stream = sys.stdout
    stream.write(ndjson_safe_stringify(obj) + "\n")
    stream.flush()


# -- Terminal Detection --

def is_interactive() -> bool:
    """Check if running in an interactive terminal (not piped)."""
    return sys.stdin.isatty() and sys.stdout.isatty()

def is_piped_input() -> bool:
    """Check if stdin has piped data."""
    return not sys.stdin.isatty()

def is_piped_output() -> bool:
    """Check if stdout is piped (not a terminal)."""
    return not sys.stdout.isatty()

def get_terminal_size() -> tuple[int, int]:
    """Get terminal (columns, rows). Returns (80, 24) as fallback."""
    try:
        size = os.get_terminal_size()
        return size.columns, size.lines
    except OSError:
        return 80, 24

def supports_color() -> bool:
    """Check if terminal supports ANSI colors."""
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    if not sys.stdout.isatty():
        return False
    term = os.environ.get("TERM", "")
    if term == "dumb":
        return False
    return True

def supports_hyperlinks() -> bool:
    """Check if terminal supports OSC 8 hyperlinks."""
    term_program = os.environ.get("TERM_PROGRAM", "")
    return term_program in ("iTerm.app", "WezTerm", "vscode")


# -- Output Formatting --

def format_hyperlink(url: str, text: str) -> str:
    """Format a clickable hyperlink (OSC 8) if supported, else plain text."""
    if supports_hyperlinks():
        return f"\033]8;;{url}\033\\{text}\033]8;;\033\\"
    return text

def truncate_middle(text: str, max_len: int = 60) -> str:
    """Truncate text in the middle for paths: src/components/.../MyComponent.tsx"""
    if len(text) <= max_len:
        return text
    half = (max_len - 3) // 2
    return text[:half] + "..." + text[-half:]

def format_duration(seconds: float) -> str:
    """Format duration as human-readable string."""
    if seconds < 1:
        return f"{seconds * 1000:.0f}ms"
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    secs = seconds % 60
    if minutes < 60:
        return f"{minutes}m {secs:.0f}s"
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours}h {mins}m"

def format_bytes(n: int) -> str:
    """Format bytes as human-readable: 1.2KB, 3.4MB."""
    if n < 1024:
        return f"{n}B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f}KB"
    if n < 1024 * 1024 * 1024:
        return f"{n / 1024 / 1024:.1f}MB"
    return f"{n / 1024 / 1024 / 1024:.1f}GB"


# -- Graceful Shutdown --

_cleanup_callbacks: list[callable] = []

def register_cleanup(callback: callable):
    """Register a cleanup callback for graceful shutdown."""
    _cleanup_callbacks.append(callback)

def run_cleanup():
    """Run all registered cleanup callbacks."""
    for cb in reversed(_cleanup_callbacks):
        try:
            cb()
        except Exception as e:
            log.debug("Cleanup error: %s", e)

def setup_signal_handlers():
    """Setup SIGINT/SIGTERM handlers for graceful shutdown."""
    import signal

    _interrupt_count = 0

    def _handler(signum, frame):
        nonlocal _interrupt_count
        _interrupt_count += 1
        if _interrupt_count >= 2:
            run_cleanup()
            sys.exit(130)
        # First interrupt -- let the application handle it
        raise KeyboardInterrupt

    try:
        signal.signal(signal.SIGINT, _handler)
        signal.signal(signal.SIGTERM, lambda s, f: (run_cleanup(), sys.exit(143)))
    except (ValueError, OSError):
        pass  # Can't set signal handlers outside main thread


# -- Safe Boundary Detection for Streaming Markdown --

def find_safe_boundary(text: str) -> int:
    """Find a safe point to split streaming text without breaking markdown.

    Avoids splitting inside:
    - Code fences (```)
    - Bold/italic markers (**text**)
    - Inline code (`code`)
    - Links ([text](url))

    Returns the index of the last safe split point.
    """
    if not text:
        return 0

    # Check for unclosed code fences
    fence_count = text.count("```")
    if fence_count % 2 != 0:
        # Inside a code fence -- find the opening
        last_fence = text.rfind("```")
        if last_fence > 0:
            return last_fence

    # Check for unclosed inline formatting
    # Walk backward to find a safe newline boundary
    for i in range(len(text) - 1, max(0, len(text) - 200), -1):
        if text[i] == "\n":
            # Check if this newline is safe (not inside code/bold/etc)
            before = text[:i]
            if before.count("`") % 2 == 0 and before.count("**") % 2 == 0:
                return i + 1

    return len(text)
