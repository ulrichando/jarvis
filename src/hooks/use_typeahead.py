"""Typeahead/autocomplete for file and command suggestions."""

from __future__ import annotations

from typing import Any, Callable, List, Optional

from .file_suggestions import SuggestionItem


class Typeahead:
    """Typeahead autocomplete combining file and command suggestions.

    Handles @ mentions, slash commands, and file path completion.

    Equivalent to useTypeahead React hook.
    """

    def __init__(
        self,
        generate_suggestions: Optional[Callable] = None,
        on_select: Optional[Callable] = None,
    ):
        self._generate_suggestions = generate_suggestions
        self._on_select = on_select
        self.suggestions: List[SuggestionItem] = []
        self.selected_index = 0
        self.is_visible = False
        self.query = ""

    async def update(self, query: str) -> None:
        self.query = query
        if not query:
            self.suggestions = []
            self.is_visible = False
            return

        if self._generate_suggestions:
            self.suggestions = await self._generate_suggestions(query)
            self.is_visible = len(self.suggestions) > 0
            self.selected_index = 0

    def select_next(self) -> None:
        if self.suggestions:
            self.selected_index = (self.selected_index + 1) % len(self.suggestions)

    def select_prev(self) -> None:
        if self.suggestions:
            self.selected_index = (self.selected_index - 1) % len(self.suggestions)

    def accept(self) -> Optional[SuggestionItem]:
        if not self.suggestions:
            return None
        selected = self.suggestions[self.selected_index]
        if self._on_select:
            self._on_select(selected)
        self.dismiss()
        return selected

    def dismiss(self) -> None:
        self.suggestions = []
        self.is_visible = False
        self.selected_index = 0
