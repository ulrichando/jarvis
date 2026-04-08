"""Log sanitization — CWE-117 defence.

Strips ANSI escape sequences and C0 control characters from strings
before they reach log files to prevent log injection/forging.

Mirrors OpenClaw's sanitizeForLog() from src/terminal/ansi.ts.

Usage:
    from src.utils.log_sanitize import sanitize_for_log, LogSanitizeFilter

    # One-shot:
    clean = sanitize_for_log(user_input)

    # Auto-sanitize all log records for a logger:
    logging.getLogger("jarvis").addFilter(LogSanitizeFilter())

    # Wire globally at startup:
    attach_sanitize_filter()
"""

from __future__ import annotations

import logging
import re

# ── Regex patterns ────────────────────────────────────────────────────────────

# Full ANSI escape sequence coverage (CSI, OSC, two-char, private sequences)
_ANSI_RE = re.compile(
    r"""
    \x1b            # ESC character
    (?:
        [@-Z\\-_]                           # two-char sequences (ESC + one byte)
      | \[[0-?]*[ -/]*[@-~]                 # CSI sequences:  ESC [ ... final
      | \][^\x07\x1b]*(?:\x07|\x1b\\)       # OSC sequences:  ESC ] ... ST
      | [()][ -~]                           # charset designation
    )
    """,
    re.VERBOSE,
)

# C0 control characters — excludes \t (0x09), \n (0x0a), \r (0x0d) which are
# legitimate in multi-line log messages.
_C0_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


# ── Core function ─────────────────────────────────────────────────────────────

def sanitize_for_log(text: str) -> str:
    """Strip ANSI escape sequences and dangerous C0 control characters.

    Safe characters preserved: tab (\\t), newline (\\n), carriage return (\\r).
    """
    text = _ANSI_RE.sub("", text)
    text = _C0_RE.sub("", text)
    return text


# ── Logging integration ───────────────────────────────────────────────────────

class LogSanitizeFilter(logging.Filter):
    """Logging Filter that sanitizes all message text and string args in-place.

    Attach to any logger or handler to automatically scrub ANSI/C0 from every
    record that passes through it.
    """

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        if isinstance(record.msg, str):
            record.msg = sanitize_for_log(record.msg)

        if record.args:
            if isinstance(record.args, tuple):
                record.args = tuple(
                    sanitize_for_log(a) if isinstance(a, str) else a
                    for a in record.args
                )
            elif isinstance(record.args, dict):
                record.args = {
                    k: sanitize_for_log(v) if isinstance(v, str) else v
                    for k, v in record.args.items()
                }

        return True  # never drop records — only clean them


def attach_sanitize_filter(logger_name: str = "jarvis") -> None:
    """Attach LogSanitizeFilter to all handlers of the given logger.

    Call this once at startup after logging is configured.
    """
    logger = logging.getLogger(logger_name)
    for handler in logger.handlers:
        handler.addFilter(LogSanitizeFilter())
    # Also attach to root logger handlers
    root = logging.getLogger()
    for handler in root.handlers:
        # Avoid double-attaching
        if not any(isinstance(f, LogSanitizeFilter) for f in handler.filters):
            handler.addFilter(LogSanitizeFilter())
