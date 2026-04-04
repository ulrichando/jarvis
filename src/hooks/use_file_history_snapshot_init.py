"""Initialize file history snapshots from a previous session."""

from __future__ import annotations

from typing import Any, Callable, List, Optional


class FileHistorySnapshotInit:
    """Initializes file history state from snapshots.

    Equivalent to useFileHistorySnapshotInit React hook.
    """

    def __init__(
        self,
        initial_snapshots: Optional[List[Any]] = None,
        file_history_state: Any = None,
        on_update_state: Optional[Callable] = None,
        file_history_enabled: Callable[[], bool] = lambda: False,
        restore_state_fn: Optional[Callable] = None,
    ):
        self._initialized = False
        self._initial_snapshots = initial_snapshots
        self._file_history_state = file_history_state
        self._on_update_state = on_update_state
        self._file_history_enabled = file_history_enabled
        self._restore_state_fn = restore_state_fn

    def initialize(self) -> None:
        """Initialize file history from snapshots if not already done."""
        if not self._file_history_enabled() or self._initialized:
            return

        self._initialized = True

        if self._initial_snapshots and self._restore_state_fn and self._on_update_state:
            self._restore_state_fn(self._initial_snapshots, self._on_update_state)
