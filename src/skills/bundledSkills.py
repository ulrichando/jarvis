"""Bundled skills registry."""

from __future__ import annotations

from typing import Any, Optional

from .bundled.index import get_bundled_skills


def load_bundled_skills() -> dict[str, Any]:
    """Load all bundled skills."""
    return get_bundled_skills()
