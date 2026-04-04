"""Manages deferred session-start hook messages.

Hook messages are injected asynchronously when the promise resolves.
Returns a callback that onSubmit should call before the first API
request to ensure the model always sees hook context.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, List, Optional


class DeferredHookMessages:
    """Manages deferred SessionStart hook messages.

    Equivalent to useDeferredHookMessages React hook.
    """

    def __init__(
        self,
        pending_messages: Optional[asyncio.Future] = None,
        set_messages: Optional[Callable] = None,
    ):
        self._pending = pending_messages
        self._set_messages = set_messages
        self._resolved = pending_messages is None
        self._result: List[Any] = []

        if pending_messages is not None and set_messages is not None:
            asyncio.ensure_future(self._resolve())

    async def _resolve(self) -> None:
        if self._pending is None:
            return
        try:
            msgs = await self._pending
            self._resolved = True
            self._result = msgs
            if msgs and self._set_messages:
                self._set_messages(msgs)
        except Exception:
            self._resolved = True

    async def ensure_resolved(self) -> None:
        """Wait for pending messages to resolve.

        Call this before the first API request to ensure the model
        sees hook context.
        """
        if self._resolved or self._pending is None:
            return
        try:
            msgs = await self._pending
            if not self._resolved:
                self._resolved = True
                self._result = msgs
                if msgs and self._set_messages:
                    self._set_messages(msgs)
        except Exception:
            self._resolved = True

    @property
    def is_resolved(self) -> bool:
        return self._resolved

    @property
    def messages(self) -> List[Any]:
        return self._result
