"""useTabStatus hook - manage tab status indicator."""
from __future__ import annotations
from typing import Any

from ..termio.osc import tab_status, supports_tab_status


class UseTabStatus:
    """Manages the OSC 21337 tab-status indicator."""

    def __init__(self) -> None:
        self._fields: dict[str, Any] = {}

    def set_indicator(self, color: dict[str, Any] | None) -> str:
        """Set the tab indicator color. Returns escape sequence."""
        self._fields["indicator"] = color
        if supports_tab_status():
            return tab_status({"indicator": color})
        return ""

    def set_status(self, text: str | None) -> str:
        """Set the tab status text. Returns escape sequence."""
        self._fields["status"] = text
        if supports_tab_status():
            return tab_status({"status": text})
        return ""

    def clear(self) -> str:
        """Clear all tab status fields."""
        self._fields = {}
        if supports_tab_status():
            return tab_status({"indicator": None, "status": None, "statusColor": None})
        return ""
