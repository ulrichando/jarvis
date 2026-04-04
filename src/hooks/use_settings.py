"""Settings access from application state."""

from __future__ import annotations

from typing import Any, Dict


def get_settings(app_state: Dict[str, Any]) -> Dict[str, Any]:
    """Access current settings from app state.

    Equivalent to useSettings React hook.

    Args:
        app_state: The application state dictionary.

    Returns:
        The settings dictionary (read-only view).
    """
    return app_state.get("settings", {})
