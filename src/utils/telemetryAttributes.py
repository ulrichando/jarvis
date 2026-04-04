"""
Telemetry attributes for observability.
"""

import os
from typing import Any, Dict


def get_telemetry_attributes() -> Dict[str, Any]:
    """Get telemetry attributes for the current session."""
    attributes: Dict[str, Any] = {}

    user_id = os.environ.get("JARVIS_USER_ID", "")
    if user_id:
        attributes["user.id"] = user_id

    session_id = os.environ.get("JARVIS_SESSION_ID", "")
    if session_id:
        attributes["session.id"] = session_id

    terminal = os.environ.get("TERM_PROGRAM", "")
    if terminal:
        attributes["terminal.type"] = terminal

    return attributes
