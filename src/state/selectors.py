"""State selectors for deriving computed values from app state."""

from __future__ import annotations

from typing import Any, Optional


def get_active_session(state: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Get the active session from state."""
    return state.get("activeSession")


def get_is_busy(state: dict[str, Any]) -> bool:
    """Check if the app is currently busy processing."""
    return state.get("isBusy", False)


def get_current_model(state: dict[str, Any]) -> Optional[str]:
    """Get the currently active model."""
    return state.get("currentModel")


def get_messages(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Get all messages in the current session."""
    return state.get("messages", [])
