"""Terminal capability querier.

Sends queries to the terminal to detect capabilities like
synchronized update, Kitty keyboard protocol, etc.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TerminalCapabilities:
    """Detected terminal capabilities."""
    supports_sync_update: bool = False
    supports_kitty_keyboard: bool = False
    supports_focus_events: bool = False
    terminal_name: str | None = None
    background_color: dict[str, Any] | None = None


class TerminalQuerier:
    """Queries terminal for capability detection."""

    def __init__(self) -> None:
        self.capabilities = TerminalCapabilities()
        self._pending_queries: list[str] = []

    def query_capabilities(self) -> str:
        """Return escape sequences to query terminal capabilities."""
        # DECRQM for synchronized update (mode 2026)
        # DA1 as sentinel
        return ""

    def handle_response(self, response: dict[str, Any]) -> None:
        """Process a terminal response."""
        resp_type = response.get("type")
        if resp_type == "decrpm":
            mode = response.get("mode")
            status = response.get("status")
            if mode == 2026 and status in (1, 2):
                self.capabilities.supports_sync_update = True
        elif resp_type == "kittyKeyboard":
            self.capabilities.supports_kitty_keyboard = True
        elif resp_type == "xtversion":
            self.capabilities.terminal_name = response.get("name")
