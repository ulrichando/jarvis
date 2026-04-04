"""Mailbox: async message queue with filtering and waiting."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Callable, Literal, Optional

MessageSource = Literal["user", "teammate", "system", "tick", "task"]


@dataclass
class Message:
    id: str
    source: MessageSource
    content: str
    timestamp: str
    from_: Optional[str] = None
    color: Optional[str] = None


@dataclass
class _Waiter:
    fn: Callable[[Message], bool]
    future: asyncio.Future


class Mailbox:
    """Thread-safe async mailbox with poll/receive/wait semantics."""

    def __init__(self) -> None:
        self._queue: list[Message] = []
        self._waiters: list[_Waiter] = []
        self._revision: int = 0
        self._subscribers: list[Callable[[], None]] = []

    @property
    def length(self) -> int:
        return len(self._queue)

    @property
    def revision(self) -> int:
        return self._revision

    def send(self, msg: Message) -> None:
        """Send a message. If a waiter matches, deliver directly."""
        self._revision += 1
        for i, waiter in enumerate(self._waiters):
            if waiter.fn(msg):
                w = self._waiters.pop(i)
                if not w.future.done():
                    w.future.set_result(msg)
                self._notify()
                return
        self._queue.append(msg)
        self._notify()

    def poll(self, fn: Optional[Callable[[Message], bool]] = None) -> Optional[Message]:
        """Return and remove the first matching message, or None."""
        predicate = fn or (lambda _: True)
        for i, msg in enumerate(self._queue):
            if predicate(msg):
                return self._queue.pop(i)
        return None

    async def receive(self, fn: Optional[Callable[[Message], bool]] = None) -> Message:
        """Wait for a matching message. Returns immediately if one is queued."""
        predicate = fn or (lambda _: True)
        for i, msg in enumerate(self._queue):
            if predicate(msg):
                self._queue.pop(i)
                self._notify()
                return msg

        loop = asyncio.get_event_loop()
        future: asyncio.Future[Message] = loop.create_future()
        self._waiters.append(_Waiter(fn=predicate, future=future))
        return await future

    def subscribe(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Subscribe to changes. Returns an unsubscribe function."""
        self._subscribers.append(callback)

        def unsubscribe() -> None:
            try:
                self._subscribers.remove(callback)
            except ValueError:
                pass

        return unsubscribe

    def _notify(self) -> None:
        for cb in self._subscribers:
            cb()
