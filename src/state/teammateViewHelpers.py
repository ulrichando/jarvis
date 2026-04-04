"""Teammate view helpers for multi-agent display."""

from __future__ import annotations

from typing import Any, Optional


def get_teammate_display_name(teammate: dict[str, Any]) -> str:
    """Get the display name for a teammate."""
    return teammate.get("name", teammate.get("id", "Unknown"))


def get_teammate_status(teammate: dict[str, Any]) -> str:
    """Get the status string for a teammate."""
    return teammate.get("status", "idle")


def format_teammate_activity(activity: Optional[dict[str, Any]]) -> str:
    """Format teammate activity for display."""
    if not activity:
        return ""
    return activity.get("summary", "")
