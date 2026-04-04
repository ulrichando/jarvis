"""Pagination utilities for plugin listings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Generic, TypeVar

T = TypeVar("T")


@dataclass
class PaginationState(Generic[T]):
    """State for paginated listings."""
    items: list[T]
    page: int = 0
    page_size: int = 10

    @property
    def total_pages(self) -> int:
        return max(1, (len(self.items) + self.page_size - 1) // self.page_size)

    @property
    def current_items(self) -> list[T]:
        start = self.page * self.page_size
        end = start + self.page_size
        return self.items[start:end]

    @property
    def has_next(self) -> bool:
        return self.page < self.total_pages - 1

    @property
    def has_prev(self) -> bool:
        return self.page > 0

    def next_page(self) -> None:
        if self.has_next:
            self.page += 1

    def prev_page(self) -> None:
        if self.has_prev:
            self.page -= 1
