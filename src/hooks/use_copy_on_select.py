"""Auto-copy selection to clipboard on mouse-up or multi-click."""

from __future__ import annotations

from typing import Any, Callable, Optional, Protocol


class Selection(Protocol):
    """Protocol for selection state."""

    def subscribe(self, callback: Callable) -> Callable:
        ...

    def get_state(self) -> Optional[dict]:
        ...

    def has_selection(self) -> bool:
        ...

    def copy_selection_no_clear(self) -> Optional[str]:
        ...


class CopyOnSelectHandler:
    """Auto-copy the selection to clipboard when the user finishes dragging.

    Mirrors iTerm2's 'Copy to pasteboard on selection'.

    Equivalent to useCopyOnSelect React hook.
    """

    def __init__(
        self,
        selection: Selection,
        is_active: bool = True,
        on_copied: Optional[Callable[[str], None]] = None,
        copy_enabled: bool = True,
    ):
        self.selection = selection
        self.is_active = is_active
        self.on_copied = on_copied
        self.copy_enabled = copy_enabled
        self._copied = False
        self._unsubscribe: Optional[Callable] = None

        if is_active:
            self._subscribe()

    def _subscribe(self) -> None:
        self._unsubscribe = self.selection.subscribe(self._on_selection_change)

    def _on_selection_change(self) -> None:
        if not self.is_active:
            return

        state = self.selection.get_state()
        has = self.selection.has_selection()

        # Drag in progress
        if state and state.get("is_dragging"):
            self._copied = False
            return

        # No selection
        if not has:
            self._copied = False
            return

        # Already copied this selection
        if self._copied:
            return

        if not self.copy_enabled:
            return

        text = self.selection.copy_selection_no_clear()
        if not text or not text.strip():
            self._copied = True
            return

        self._copied = True
        if self.on_copied:
            self.on_copied(text)

    def dispose(self) -> None:
        """Clean up subscription."""
        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None
