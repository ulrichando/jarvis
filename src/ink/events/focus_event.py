"""Focus event for component focus changes.

Dispatched when focus moves between elements. 'focus' fires on the newly
focused element, 'blur' fires on the previously focused one. Both bubble.
"""

from __future__ import annotations

from typing import Any, Literal

from .terminal_event import TerminalEvent, TerminalEventInit


class FocusEvent(TerminalEvent):
    """Focus/blur event."""

    def __init__(
        self,
        type_: Literal["focus", "blur"],
        related_target: Any | None = None,
    ) -> None:
        super().__init__(type_, TerminalEventInit(bubbles=True, cancelable=False))
        self.related_target: Any | None = related_target
