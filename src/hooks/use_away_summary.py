"""Generate 'while you were away' summary after terminal idle."""

from __future__ import annotations

from typing import Any, Callable, List, Optional

BLUR_DELAY_MS = 5 * 60_000


class AwaySummaryManager:
    """Appends a 'while you were away' summary after terminal blur.

    Fires when: (a) 5min since blur, (b) no turn in progress,
    (c) no existing away_summary since last user message.

    Equivalent to useAwaySummary React hook.
    """

    def __init__(
        self,
        get_messages: Callable[[], List[Any]],
        set_messages: Callable,
        generate_summary: Optional[Callable] = None,
        is_loading_fn: Callable[[], bool] = lambda: False,
        enabled: bool = False,
    ):
        self._get_messages = get_messages
        self._set_messages = set_messages
        self._generate_summary = generate_summary
        self._is_loading_fn = is_loading_fn
        self._enabled = enabled
        self._pending = False

    def on_blur(self) -> None:
        if not self._enabled:
            return

    def on_focus(self) -> None:
        self._pending = False

    async def generate(self) -> None:
        if not self._enabled or not self._generate_summary:
            return
        messages = self._get_messages()
        if self._has_summary_since_last_user(messages):
            return
        text = await self._generate_summary(messages)
        if text:
            self._set_messages(text)

    @staticmethod
    def _has_summary_since_last_user(messages: List[Any]) -> bool:
        for m in reversed(messages):
            t = m.get("type") if isinstance(m, dict) else getattr(m, "type", None)
            if t == "user":
                return False
            st = m.get("subtype") if isinstance(m, dict) else getattr(m, "subtype", None)
            if t == "system" and st == "away_summary":
                return True
        return False
