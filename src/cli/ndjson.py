"""NDJSON-safe JSON serialization."""

from __future__ import annotations

import json
from typing import Any


def _escape_js_line_terminators(s: str) -> str:
    """Escape U+2028/U+2029 so serialized output cannot be broken by line-splitting."""
    return s.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def ndjson_safe_stringify(value: Any) -> str:
    """JSON serialize for one-message-per-line transports.

    Escapes U+2028 LINE SEPARATOR and U+2029 PARAGRAPH SEPARATOR.
    """
    return _escape_js_line_terminators(json.dumps(value, default=str))
