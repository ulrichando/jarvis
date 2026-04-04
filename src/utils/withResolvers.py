"""
Polyfill for Promise.withResolvers pattern in Python using asyncio.Future.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass
class Resolvers(Generic[T]):
    future: asyncio.Future[T]

    def resolve(self, value: T) -> None:
        if not self.future.done():
            self.future.set_result(value)

    def reject(self, reason: Exception) -> None:
        if not self.future.done():
            self.future.set_exception(reason)


def with_resolvers() -> Resolvers:
    """
    Create a Future with exposed resolve and reject methods.
    Equivalent to Promise.withResolvers() in JavaScript.
    """
    loop = asyncio.get_event_loop()
    future: asyncio.Future = loop.create_future()
    return Resolvers(future=future)
