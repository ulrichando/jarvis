"""
Generic observable store with listener subscription pattern.
Python equivalent of store.ts.
"""
from __future__ import annotations

from typing import Callable, Generic, Optional, TypeVar

T = TypeVar("T")

Listener = Callable[[], None]
OnChange = Callable[[dict], None]  # receives {"new_state": T, "old_state": T}


class Store(Generic[T]):
    """Minimal observable state store with subscriber notifications."""

    def __init__(
        self,
        initial_state: T,
        on_change: Optional[Callable[[T, T], None]] = None,
    ) -> None:
        self._state: T = initial_state
        self._listeners: set[Listener] = set()
        self._on_change = on_change

    def get_state(self) -> T:
        return self._state

    def set_state(self, updater: Callable[[T], T]) -> None:
        prev = self._state
        next_state = updater(prev)
        if next_state is prev:
            return
        self._state = next_state
        if self._on_change is not None:
            self._on_change(next_state, prev)
        for listener in list(self._listeners):
            listener()

    def subscribe(self, listener: Listener) -> Callable[[], None]:
        self._listeners.add(listener)

        def unsubscribe() -> None:
            self._listeners.discard(listener)

        return unsubscribe


def create_store(
    initial_state: T,
    on_change: Optional[Callable[[T, T], None]] = None,
) -> Store[T]:
    """Factory matching the TypeScript createStore API."""
    return Store(initial_state, on_change)
