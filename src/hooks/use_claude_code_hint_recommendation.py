"""JARVIS hint recommendation notification."""

from __future__ import annotations

from typing import Any, Callable, Optional


class ClaudeCodeHintRecommendation:
    """Manages JARVIS hint/recommendation notifications.

    Equivalent to useClaudeCodeHintRecommendation React hook.
    """

    def __init__(
        self,
        add_notification: Optional[Callable] = None,
        enabled: bool = True,
    ):
        self._add_notification = add_notification
        self._enabled = enabled
        self._shown = False

    def check(self) -> None:
        if not self._enabled or self._shown:
            return
        # Check conditions for showing hint
        pass

    def dismiss(self) -> None:
        self._shown = True
