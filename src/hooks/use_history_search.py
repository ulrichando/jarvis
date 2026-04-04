"""Interactive history search (Ctrl+R style)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Set


@dataclass
class HistoryEntry:
    display: str
    pasted_contents: Dict[int, Any] = None

    def __post_init__(self):
        if self.pasted_contents is None:
            self.pasted_contents = {}


class HistorySearch:
    """Interactive history search with incremental matching.

    Equivalent to useHistorySearch React hook.
    """

    def __init__(
        self,
        on_accept_history: Callable[[HistoryEntry], None],
        on_input_change: Callable[[str], None],
        on_cursor_change: Callable[[int], None],
        on_mode_change: Callable[[str], None],
        set_pasted_contents: Callable,
        make_history_reader: Optional[Callable] = None,
    ):
        self._on_accept_history = on_accept_history
        self._on_input_change = on_input_change
        self._on_cursor_change = on_cursor_change
        self._on_mode_change = on_mode_change
        self._set_pasted_contents = set_pasted_contents
        self._make_history_reader = make_history_reader

        self.query = ""
        self.match: Optional[HistoryEntry] = None
        self.failed_match = False
        self.is_searching = False

        self._original_input = ""
        self._original_cursor = 0
        self._original_mode = "prompt"
        self._original_pasted = {}
        self._seen_prompts: Set[str] = set()
        self._history_reader = None

    def start_search(self, current_input: str, cursor: int, mode: str, pasted: dict) -> None:
        self.is_searching = True
        self._original_input = current_input
        self._original_cursor = cursor
        self._original_mode = mode
        self._original_pasted = pasted
        self._seen_prompts.clear()
        if self._make_history_reader:
            self._history_reader = self._make_history_reader()

    def cancel(self) -> None:
        self._on_input_change(self._original_input)
        self._on_cursor_change(self._original_cursor)
        self._set_pasted_contents(self._original_pasted)
        self.reset()

    def accept(self) -> None:
        if self.match:
            self._on_input_change(self.match.display)
            self._set_pasted_contents(self.match.pasted_contents)
        else:
            self._set_pasted_contents(self._original_pasted)
        self.reset()

    def execute(self) -> None:
        if not self.query and self._original_input:
            self._on_accept_history(HistoryEntry(
                display=self._original_input,
                pasted_contents=self._original_pasted,
            ))
        elif self.match:
            self._on_accept_history(self.match)
        self.reset()

    def set_query(self, q: str) -> None:
        self.query = q

    def reset(self) -> None:
        self.is_searching = False
        self.query = ""
        self.failed_match = False
        self.match = None
        self._seen_prompts.clear()
        self._history_reader = None
