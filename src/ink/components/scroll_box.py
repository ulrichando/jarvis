"""ScrollBox component - scrollable container."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class ScrollBoxProps:
    height: int | str | None = None
    scroll_top: int = 0
    sticky_scroll: bool = False
    on_scroll: Callable[[int], None] | None = None


class ScrollBox:
    """A scrollable container that clips content and supports scrollTop-based scrolling."""

    def __init__(self, props: ScrollBoxProps | None = None) -> None:
        self.props = props or ScrollBoxProps()
        self._scroll_top = 0
        self._scroll_height = 0
        self._viewport_height = 0

    @property
    def scroll_top(self) -> int:
        return self._scroll_top

    @scroll_top.setter
    def scroll_top(self, value: int) -> None:
        self._scroll_top = max(0, value)

    def scroll_to(self, position: int) -> None:
        self.scroll_top = position

    def scroll_by(self, delta: int) -> None:
        self.scroll_top = self._scroll_top + delta
