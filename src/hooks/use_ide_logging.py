"""Forward IDE log events to analytics."""

from __future__ import annotations

from typing import Callable, List, Optional


class IdeLogging:
    """Registers notification handler for IDE log events.

    Equivalent to useIdeLogging React hook.
    """

    def __init__(
        self,
        log_event: Callable,
        get_ide_client: Optional[Callable] = None,
    ):
        self._log_event = log_event
        self._get_ide_client = get_ide_client

    def handle_notification(self, data: dict) -> None:
        params = data.get("params", {})
        event_name = params.get("eventName", "")
        event_data = params.get("eventData", {})
        self._log_event(f"ide_{event_name}", event_data)
