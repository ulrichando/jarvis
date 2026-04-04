"""
UUID validation and agent ID generation utilities.
"""

from __future__ import annotations

import os
import re
import uuid as uuid_module
from typing import Optional

UUID_REGEX = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def validate_uuid(maybe_uuid: object) -> Optional[str]:
    """
    Validate a UUID string.

    Args:
        maybe_uuid: The value to check.

    Returns:
        The UUID string if valid, None otherwise.
    """
    if not isinstance(maybe_uuid, str):
        return None
    return maybe_uuid if UUID_REGEX.match(maybe_uuid) else None


def create_agent_id(label: Optional[str] = None) -> str:
    """
    Generate a new agent ID with prefix for consistency with task IDs.
    Format: a{label-}{16 hex chars}
    """
    suffix = os.urandom(8).hex()
    if label:
        return f"a{label}-{suffix}"
    return f"a{suffix}"
