"""Formatters for LSP results."""
from __future__ import annotations

from typing import Any


def format_diagnostics(diagnostics: list[dict[str, Any]]) -> str:
    """Format LSP diagnostics into a readable string."""
    if not diagnostics:
        return "No diagnostics found."
    lines = []
    for d in diagnostics:
        severity = d.get("severity", "unknown")
        message = d.get("message", "")
        range_ = d.get("range", {})
        start = range_.get("start", {})
        line = start.get("line", 0) + 1
        col = start.get("character", 0) + 1
        lines.append(f"  Line {line}:{col} [{severity}] {message}")
    return "\n".join(lines)


def format_hover(hover: dict[str, Any]) -> str:
    """Format LSP hover information."""
    contents = hover.get("contents", "")
    if isinstance(contents, str):
        return contents
    if isinstance(contents, dict):
        return contents.get("value", str(contents))
    if isinstance(contents, list):
        return "\n".join(str(c) for c in contents)
    return str(contents)


def format_locations(locations: list[dict[str, Any]]) -> str:
    """Format LSP locations (definitions/references)."""
    if not locations:
        return "No locations found."
    lines = []
    for loc in locations:
        uri = loc.get("uri", "")
        range_ = loc.get("range", {})
        start = range_.get("start", {})
        line = start.get("line", 0) + 1
        lines.append(f"  {uri}:{line}")
    return "\n".join(lines)
