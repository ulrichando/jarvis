"""useAnimationFrame hook - call a function on each frame."""
from __future__ import annotations
from typing import Callable


class UseAnimationFrame:
    """Calls a function on each animation frame (~60fps)."""

    def __init__(self, callback: Callable[[], None]) -> None:
        self.callback = callback
        self._active = True

    def tick(self) -> None:
        if self._active:
            self.callback()

    def stop(self) -> None:
        self._active = False
