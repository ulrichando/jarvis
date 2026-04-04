"""IDE integration combining selection, at-mention, and logging."""

from __future__ import annotations

from typing import Any, Callable, List, Optional

from .use_ide_at_mentioned import IDEAtMentioned, IdeAtMentionedTracker
from .use_ide_connection_status import IdeConnectionResult, get_ide_connection_status
from .use_ide_logging import IdeLogging
from .use_ide_selection import IDESelection, IdeSelectionTracker


class IDEIntegration:
    """Full IDE integration: selection tracking, at-mentions, logging, connection status.

    Equivalent to useIDEIntegration React hook.
    """

    def __init__(
        self,
        mcp_clients: Optional[List[dict]] = None,
        on_at_mentioned: Optional[Callable] = None,
        on_selection: Optional[Callable] = None,
        log_event: Optional[Callable] = None,
    ):
        self.mcp_clients = mcp_clients or []
        self.connection_status = get_ide_connection_status(mcp_clients)

        self._at_mention_tracker = IdeAtMentionedTracker(
            on_at_mentioned=on_at_mentioned or (lambda x: None)
        )
        self._selection_tracker = IdeSelectionTracker(
            on_select=on_selection or (lambda x: None)
        )
        self._logging = IdeLogging(log_event=log_event or (lambda *a: None))

    def update_clients(self, mcp_clients: List[dict]) -> None:
        self.mcp_clients = mcp_clients
        self.connection_status = get_ide_connection_status(mcp_clients)
