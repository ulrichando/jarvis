"""useSearchHighlight hook - manage search highlight state."""
from __future__ import annotations
from dataclasses import dataclass


@dataclass
class SearchHighlightState:
    query: str = ""
    current_match: int = 0
    total_matches: int = 0


class UseSearchHighlight:
    """Manages search highlight state for the screen buffer."""

    def __init__(self) -> None:
        self.state = SearchHighlightState()

    def set_query(self, query: str) -> None:
        self.state.query = query
        self.state.current_match = 0

    def next_match(self) -> None:
        if self.state.total_matches > 0:
            self.state.current_match = (self.state.current_match + 1) % self.state.total_matches

    def prev_match(self) -> None:
        if self.state.total_matches > 0:
            self.state.current_match = (self.state.current_match - 1) % self.state.total_matches

    def clear(self) -> None:
        self.state = SearchHighlightState()
