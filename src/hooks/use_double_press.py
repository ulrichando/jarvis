"""Double-press detection for keyboard shortcuts."""

from __future__ import annotations

import time
from typing import Callable, Optional

DOUBLE_PRESS_TIMEOUT_MS = 800


class DoublePressHandler:
    """Detects double-press patterns within a timeout window.

    First press triggers on_first_press callback and sets pending state.
    Second press within timeout triggers on_double_press callback.

    Equivalent to useDoublePress React hook.
    """

    def __init__(
        self,
        set_pending: Callable[[bool], None],
        on_double_press: Callable[[], None],
        on_first_press: Optional[Callable[[], None]] = None,
        timeout_ms: int = DOUBLE_PRESS_TIMEOUT_MS,
    ):
        self.set_pending = set_pending
        self.on_double_press = on_double_press
        self.on_first_press = on_first_press
        self.timeout_ms = timeout_ms
        self._last_press_time: float = 0
        self._pending: bool = False
        self._timer_handle: Optional[object] = None

    def press(self) -> None:
        """Handle a press event. Detects double-press pattern."""
        now = time.time() * 1000
        time_since_last = now - self._last_press_time
        is_double = (
            time_since_last <= self.timeout_ms and self._pending
        )

        if is_double:
            self._pending = False
            self.set_pending(False)
            self.on_double_press()
        else:
            if self.on_first_press:
                self.on_first_press()
            self._pending = True
            self.set_pending(True)

            # Auto-reset after timeout
            # In a real async context, you'd use asyncio.call_later
            # For synchronous use, the timeout is checked on next press

        self._last_press_time = now

    def reset(self) -> None:
        """Reset the double-press state."""
        self._pending = False
        self._last_press_time = 0
        self.set_pending(False)
