"""Debug utilities for bridge logging."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)

DEBUG_MSG_LIMIT = 2000
SECRET_FIELD_NAMES = [
    "session_ingress_token", "environment_secret", "access_token", "secret", "token",
]
SECRET_PATTERN = re.compile(
    r'"(' + "|".join(SECRET_FIELD_NAMES) + r')"\s*:\s*"([^"]*)"'
)
REDACT_MIN_LENGTH = 16


def redact_secrets(s: str) -> str:
    """Redact secret values in JSON strings."""
    def _replace(m: re.Match) -> str:
        field = m.group(1)
        value = m.group(2)
        if len(value) < REDACT_MIN_LENGTH:
            return f'"{field}":"[REDACTED]"'
        redacted = f"{value[:8]}...{value[-4:]}"
        return f'"{field}":"{redacted}"'
    return SECRET_PATTERN.sub(_replace, s)


def debug_truncate(s: str) -> str:
    """Truncate a string for debug logging, collapsing newlines."""
    flat = s.replace("\n", "\\n")
    if len(flat) <= DEBUG_MSG_LIMIT:
        return flat
    return flat[:DEBUG_MSG_LIMIT] + f"... ({len(flat)} chars)"


def debug_body(data: Any) -> str:
    """Truncate a JSON-serializable value for debug logging."""
    raw = data if isinstance(data, str) else json.dumps(data, default=str)
    s = redact_secrets(raw)
    if len(s) <= DEBUG_MSG_LIMIT:
        return s
    return s[:DEBUG_MSG_LIMIT] + f"... ({len(s)} chars)"


def describe_error(err: Any) -> str:
    """Extract a descriptive error message."""
    msg = str(err)
    if hasattr(err, "response") and err.response is not None:
        data = getattr(err.response, "data", None) or getattr(err.response, "json", lambda: None)()
        if isinstance(data, dict):
            detail = data.get("message")
            if not detail:
                error_obj = data.get("error")
                if isinstance(error_obj, dict):
                    detail = error_obj.get("message")
            if detail:
                return f"{msg}: {detail}"
    return msg


def extract_http_status(err: Any) -> Optional[int]:
    """Extract HTTP status code from an error."""
    resp = getattr(err, "response", None)
    if resp is not None:
        status = getattr(resp, "status", None) or getattr(resp, "status_code", None)
        if isinstance(status, int):
            return status
    return None


def extract_error_detail(data: Any) -> Optional[str]:
    """Pull a human-readable message out of an API error response body."""
    if not isinstance(data, dict):
        return None
    if isinstance(data.get("message"), str):
        return data["message"]
    error = data.get("error")
    if isinstance(error, dict) and isinstance(error.get("message"), str):
        return error["message"]
    return None


def log_bridge_skip(reason: str, debug_msg: Optional[str] = None, v2: Optional[bool] = None) -> None:
    """Log a bridge init skip."""
    if debug_msg:
        logger.debug(debug_msg)
    logger.info("tengu_bridge_repl_skipped reason=%s v2=%s", reason, v2)
