"""
Crypto utilities -- thin wrapper around uuid.
"""

from __future__ import annotations

import uuid


def random_uuid() -> str:
    """Generate a random UUID v4 string."""
    return str(uuid.uuid4())
