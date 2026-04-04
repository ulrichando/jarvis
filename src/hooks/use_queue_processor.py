"""Process queued commands when conditions are met."""

from __future__ import annotations

import asyncio
from typing import Any, Callable, List, Optional, Protocol


class QueryGuard(Protocol):
    """Protocol for query guard that tracks active query state."""

    @property
    def is_active(self) -> bool:
        ...

    def subscribe(self, callback: Callable) -> Callable:
        ...


class QueueProcessor:
    """Processes queued commands when conditions are met.

    Processing triggers when:
    - No query active (query_guard)
    - Queue has items
    - No active local JSX UI blocking input

    Equivalent to useQueueProcessor React hook.
    """

    def __init__(
        self,
        execute_queued_input: Callable,
        query_guard: QueryGuard,
        get_queue_snapshot: Callable[[], list],
        subscribe_queue: Callable,
    ):
        self.execute_queued_input = execute_queued_input
        self.query_guard = query_guard
        self._get_queue_snapshot = get_queue_snapshot
        self._subscribe_queue = subscribe_queue
        self._has_active_local_jsx_ui = False

    def set_has_active_local_jsx_ui(self, active: bool) -> None:
        self._has_active_local_jsx_ui = active

    def try_process(self) -> None:
        """Try to process queued commands if conditions are met."""
        if self.query_guard.is_active:
            return
        if self._has_active_local_jsx_ui:
            return

        queue = self._get_queue_snapshot()
        if not queue:
            return

        # Process the queue
        self.execute_queued_input(queue)
