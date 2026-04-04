"""Show notification when clipboard contains an image on focus regain."""

from __future__ import annotations

import time
from typing import Callable, Optional

FOCUS_CHECK_DEBOUNCE_MS = 1000
HINT_COOLDOWN_MS = 30000


class ClipboardImageHint:
    """Shows a notification when the terminal regains focus and clipboard has an image.

    Equivalent to useClipboardImageHint React hook.
    """

    def __init__(
        self,
        add_notification: Callable,
        has_image_in_clipboard: Optional[Callable] = None,
        enabled: bool = True,
    ):
        self._add_notification = add_notification
        self._has_image_in_clipboard = has_image_in_clipboard
        self._enabled = enabled
        self._last_focused = True
        self._last_hint_time: float = 0

    async def on_focus_change(self, is_focused: bool) -> None:
        was_focused = self._last_focused
        self._last_focused = is_focused

        if not self._enabled or not is_focused or was_focused:
            return

        now = time.time() * 1000
        if now - self._last_hint_time < HINT_COOLDOWN_MS:
            return

        if self._has_image_in_clipboard and await self._has_image_in_clipboard():
            self._last_hint_time = now
            self._add_notification(
                key="clipboard-image-hint",
                text="Image in clipboard - paste to attach",
                timeout_ms=8000,
            )
