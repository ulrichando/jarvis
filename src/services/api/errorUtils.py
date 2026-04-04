"""API error utilities -- converted from TypeScript."""

from __future__ import annotations

from typing import Any, Dict, Optional


def extract_connection_error_details(error: Exception) -> Optional[Dict[str, Any]]:
    """Extract details from a connection error."""
    cause = getattr(error, "__cause__", None) or getattr(error, "cause", None)
    if cause and hasattr(cause, "errno"):
        return {"code": getattr(cause, "errno", None)}
    return None
