"""Index of all bundled skills."""

from __future__ import annotations

from typing import Any


def get_bundled_skills() -> dict[str, Any]:
    """Get all bundled skills."""
    return {
        "batch": {"name": "batch", "description": "Run multiple commands in batch"},
        "debug": {"name": "debug", "description": "Debug assistance"},
        "remember": {"name": "remember", "description": "Store information for later recall"},
        "simplify": {"name": "simplify", "description": "Simplify complex code or text"},
        "verify": {"name": "verify", "description": "Verify code changes"},
        "loop": {"name": "loop", "description": "Iterative task execution"},
        "stuck": {"name": "stuck", "description": "Help when stuck on a problem"},
    }
