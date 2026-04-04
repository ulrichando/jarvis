"""Virtual scrolling for large message lists."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

DEFAULT_ESTIMATE = 3
OVERSCAN_ROWS = 80
COLD_START_COUNT = 30
SCROLL_QUANTIZE = 4


@dataclass
class VirtualScrollRange:
    start: int
    end: int
    total: int


class VirtualScroll:
    """Virtual scrolling for large item lists.

    Only renders items in/near the viewport, using estimated heights
    for unmeasured items.

    Equivalent to useVirtualScroll React hook.
    """

    def __init__(
        self,
        item_count: int = 0,
        viewport_height: int = 0,
        default_estimate: int = DEFAULT_ESTIMATE,
        overscan: int = OVERSCAN_ROWS,
    ):
        self._item_count = item_count
        self._viewport_height = viewport_height
        self._default_estimate = default_estimate
        self._overscan = overscan
        self._scroll_top = 0
        self._heights: Dict[int, int] = {}

    def set_item_count(self, count: int) -> None:
        self._item_count = count

    def set_viewport_height(self, height: int) -> None:
        self._viewport_height = height

    def set_scroll_top(self, scroll_top: int) -> None:
        self._scroll_top = scroll_top

    def set_item_height(self, index: int, height: int) -> None:
        self._heights[index] = height

    def get_visible_range(self) -> VirtualScrollRange:
        """Calculate the range of items to render."""
        if self._item_count == 0:
            return VirtualScrollRange(start=0, end=0, total=0)

        if self._viewport_height == 0:
            return VirtualScrollRange(
                start=max(0, self._item_count - COLD_START_COUNT),
                end=self._item_count,
                total=self._item_count,
            )

        # Calculate visible range based on scroll position
        cumulative = 0
        start = 0
        for i in range(self._item_count):
            h = self._heights.get(i, self._default_estimate)
            if cumulative + h > self._scroll_top - self._overscan:
                start = i
                break
            cumulative += h

        end = start
        visible_height = 0
        for i in range(start, self._item_count):
            h = self._heights.get(i, self._default_estimate)
            visible_height += h
            end = i + 1
            if visible_height > self._viewport_height + self._overscan * 2:
                break

        return VirtualScrollRange(
            start=max(0, start),
            end=min(self._item_count, end),
            total=self._item_count,
        )

    @property
    def total_height(self) -> int:
        return sum(
            self._heights.get(i, self._default_estimate)
            for i in range(self._item_count)
        )
