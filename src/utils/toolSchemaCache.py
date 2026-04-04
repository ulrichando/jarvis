"""
Session-scoped cache of rendered tool schemas.

Memoizing per-session locks the schema bytes at first render so
mid-session changes no longer bust the cache.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class CachedSchema:
    """Cached tool schema with optional strict/streaming flags."""
    name: str
    description: str
    input_schema: Dict[str, Any]
    strict: Optional[bool] = None
    eager_input_streaming: Optional[bool] = None


_TOOL_SCHEMA_CACHE: Dict[str, CachedSchema] = {}


def get_tool_schema_cache() -> Dict[str, CachedSchema]:
    return _TOOL_SCHEMA_CACHE


def clear_tool_schema_cache() -> None:
    _TOOL_SCHEMA_CACHE.clear()
