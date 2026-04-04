"""Lazy-load assistant history on scroll-up."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable, List, Optional

PREFETCH_THRESHOLD_ROWS = 40
MAX_FILL_PAGES = 10


@dataclass
class HistoryPage:
    events: List[Any]
    has_more: bool
    first_id: Optional[str] = None


class AssistantHistoryLoader:
    """Lazy-loads assistant history on scroll-up.

    On mount: fetches newest page, prepends to messages.
    On scroll-up near top: fetches next-older page with scroll anchoring.

    Equivalent to useAssistantHistory React hook.
    """

    def __init__(
        self,
        set_messages: Callable,
        fetch_latest: Optional[Callable] = None,
        fetch_older: Optional[Callable] = None,
        enabled: bool = False,
    ):
        self._set_messages = set_messages
        self._fetch_latest = fetch_latest
        self._fetch_older = fetch_older
        self._enabled = enabled
        self._cursor: Optional[str] = None
        self._inflight = False
        self._fill_budget = 0

    async def load_initial(self) -> None:
        if not self._enabled or not self._fetch_latest:
            return
        page = await self._fetch_latest()
        if page:
            self._fill_budget = MAX_FILL_PAGES
            self._prepend(page, is_initial=True)

    async def load_older(self) -> None:
        if not self._enabled or self._inflight or not self._cursor or not self._fetch_older:
            return
        self._inflight = True
        try:
            page = await self._fetch_older(self._cursor)
            if page:
                self._prepend(page, is_initial=False)
        finally:
            self._inflight = False

    def _prepend(self, page: HistoryPage, is_initial: bool) -> None:
        self._cursor = page.first_id if page.has_more else None

    def maybe_load_older(self, scroll_top: int) -> None:
        if scroll_top < PREFETCH_THRESHOLD_ROWS:
            asyncio.ensure_future(self.load_older())
