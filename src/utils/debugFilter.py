"""
Debug filter for controlling which debug messages are shown.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Optional


@dataclass
class DebugFilter:
    include: list[str]
    exclude: list[str]
    is_exclusive: bool


@lru_cache(maxsize=32)
def parse_debug_filter(filter_string: Optional[str] = None) -> Optional[DebugFilter]:
    """
    Parse debug filter string into a filter configuration.

    Examples:
        "api,hooks" -> include only api and hooks categories
        "!1p,!file" -> exclude logging and file categories
        None/empty -> no filtering (show all)
    """
    if not filter_string or not filter_string.strip():
        return None

    filters = [f.strip() for f in filter_string.split(",") if f.strip()]

    if not filters:
        return None

    has_exclusive = any(f.startswith("!") for f in filters)
    has_inclusive = any(not f.startswith("!") for f in filters)

    if has_exclusive and has_inclusive:
        return None

    clean_filters = [f.lstrip("!").lower() for f in filters]

    return DebugFilter(
        include=[] if has_exclusive else clean_filters,
        exclude=clean_filters if has_exclusive else [],
        is_exclusive=has_exclusive,
    )


def extract_debug_categories(message: str) -> list[str]:
    """
    Extract debug categories from a message.

    Supports multiple patterns:
    - "category: message" -> ["category"]
    - "[CATEGORY] message" -> ["category"]
    - 'MCP server "name": message' -> ["mcp", "name"]
    """
    categories: list[str] = []

    # MCP server pattern
    mcp_match = re.match(r'^MCP server ["\']([^"\']+)["\']', message)
    if mcp_match:
        categories.append("mcp")
        categories.append(mcp_match.group(1).lower())
    else:
        # Simple prefix pattern
        prefix_match = re.match(r"^([^:\[]+):", message)
        if prefix_match:
            categories.append(prefix_match.group(1).strip().lower())

    # Bracket pattern
    bracket_match = re.match(r"^\[([^\]]+)\]", message)
    if bracket_match:
        categories.append(bracket_match.group(1).strip().lower())

    # 1P event pattern
    if "1p event:" in message.lower():
        categories.append("1p")

    # Secondary category pattern
    secondary_match = re.search(
        r":\s*([^:]+?)(?:\s+(?:type|mode|status|event))?:", message
    )
    if secondary_match:
        secondary = secondary_match.group(1).strip().lower()
        if len(secondary) < 30 and " " not in secondary:
            categories.append(secondary)

    return list(set(categories))


def should_show_debug_categories(
    categories: list[str], filter_config: Optional[DebugFilter]
) -> bool:
    """Check if debug message should be shown based on filter."""
    if filter_config is None:
        return True

    if not categories:
        return False

    if filter_config.is_exclusive:
        return not any(cat in filter_config.exclude for cat in categories)
    else:
        return any(cat in filter_config.include for cat in categories)


def should_show_debug_message(
    message: str, filter_config: Optional[DebugFilter]
) -> bool:
    """Main function to check if a debug message should be shown."""
    if filter_config is None:
        return True

    categories = extract_debug_categories(message)
    return should_show_debug_categories(categories, filter_config)
