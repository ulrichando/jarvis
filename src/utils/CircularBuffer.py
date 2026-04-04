"""
A fixed-size circular buffer that automatically evicts the oldest items
when the buffer is full.
"""

from __future__ import annotations

from typing import Generic, TypeVar

T = TypeVar("T")


class CircularBuffer(Generic[T]):
    """
    A fixed-size circular buffer that automatically evicts the oldest items
    when the buffer is full. Useful for maintaining a rolling window of data.
    """

    def __init__(self, capacity: int) -> None:
        self._capacity = capacity
        self._buffer: list[T | None] = [None] * capacity
        self._head = 0
        self._size = 0

    def add(self, item: T) -> None:
        """Add an item. If full, the oldest item will be evicted."""
        self._buffer[self._head] = item
        self._head = (self._head + 1) % self._capacity
        if self._size < self._capacity:
            self._size += 1

    def add_all(self, items: list[T]) -> None:
        """Add multiple items at once."""
        for item in items:
            self.add(item)

    def get_recent(self, count: int) -> list[T]:
        """Get the most recent N items from the buffer."""
        result: list[T] = []
        start = 0 if self._size < self._capacity else self._head
        available = min(count, self._size)

        for i in range(available):
            index = (start + self._size - available + i) % self._capacity
            item = self._buffer[index]
            if item is not None:
                result.append(item)

        return result

    def to_list(self) -> list[T]:
        """Get all items in order from oldest to newest."""
        if self._size == 0:
            return []

        result: list[T] = []
        start = 0 if self._size < self._capacity else self._head

        for i in range(self._size):
            index = (start + i) % self._capacity
            item = self._buffer[index]
            if item is not None:
                result.append(item)

        return result

    def clear(self) -> None:
        """Clear all items from the buffer."""
        self._buffer = [None] * self._capacity
        self._head = 0
        self._size = 0

    def __len__(self) -> int:
        """Get the current number of items in the buffer."""
        return self._size
