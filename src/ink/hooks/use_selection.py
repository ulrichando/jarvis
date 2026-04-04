"""useSelection hook - manage text selection state."""
from __future__ import annotations

from ..selection import SelectionState, create_selection_state, start_selection, update_selection, finish_selection, clear_selection


class UseSelection:
    """Manages text selection state for fullscreen mode."""

    def __init__(self) -> None:
        self.state = create_selection_state()

    def start(self, col: int, row: int) -> None:
        start_selection(self.state, col, row)

    def update(self, col: int, row: int) -> None:
        update_selection(self.state, col, row)

    def finish(self) -> None:
        finish_selection(self.state)

    def clear(self) -> None:
        clear_selection(self.state)

    @property
    def has_selection(self) -> bool:
        return self.state.anchor is not None and self.state.focus is not None
