"""Arrow key history navigation for prompt input."""

from __future__ import annotations

from typing import Callable, Dict, List, Optional
from dataclasses import dataclass


@dataclass
class HistoryEntry:
    display: str
    pasted_contents: Dict[int, dict] = None

    def __post_init__(self):
        if self.pasted_contents is None:
            self.pasted_contents = {}




class ArrowKeyHistory:
    """Navigate through command history with up/down arrow keys.

    Up arrow: previous history entry
    Down arrow: next history entry or restore original input

    Equivalent to useArrowKeyHistory React hook.
    """

    def __init__(
        self,
        get_history: Callable[[], List[str]],
        on_input_change: Callable[[str], None],
        on_cursor_change: Callable[[int], None],
    ):
        self._get_history = get_history
        self._on_input_change = on_input_change
        self._on_cursor_change = on_cursor_change
        self._history_index: int = -1
        self._original_input: str = ""
        self._navigating = False

    def handle_up(self, current_input: str) -> bool:
        """Handle up arrow. Returns True if consumed."""
        history = self._get_history()
        if not history:
            return False

        if not self._navigating:
            self._original_input = current_input
            self._navigating = True
            self._history_index = len(history) - 1
        elif self._history_index > 0:
            self._history_index -= 1
        else:
            return False

        entry = history[self._history_index]
        self._on_input_change(entry)
        self._on_cursor_change(len(entry))
        return True

    def handle_down(self) -> bool:
        """Handle down arrow. Returns True if consumed."""
        if not self._navigating:
            return False

        history = self._get_history()
        if self._history_index < len(history) - 1:
            self._history_index += 1
            entry = history[self._history_index]
            self._on_input_change(entry)
            self._on_cursor_change(len(entry))
        else:
            # Restore original
            self._navigating = False
            self._history_index = -1
            self._on_input_change(self._original_input)
            self._on_cursor_change(len(self._original_input))
        return True

    def reset(self) -> None:
        self._navigating = False
        self._history_index = -1
        self._original_input = ""
