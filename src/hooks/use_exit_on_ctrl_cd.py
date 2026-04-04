"""Handle ctrl+c and ctrl+d for exiting the application.

Uses a time-based double-press mechanism:
- First press: Shows 'Press X again to exit' message
- Second press within timeout: Exits the application
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from .use_double_press import DoublePressHandler


@dataclass
class ExitState:
    pending: bool = False
    key_name: Optional[str] = None  # 'Ctrl-C', 'Ctrl-D', or None


class ExitOnCtrlCD:
    """Handle ctrl+c and ctrl+d for exiting the application.

    Uses a time-based double-press mechanism.

    Equivalent to useExitOnCtrlCD React hook.
    """

    def __init__(
        self,
        exit_fn: Callable[[], None],
        on_interrupt: Optional[Callable[[], bool]] = None,
    ):
        self._exit_fn = exit_fn
        self._on_interrupt = on_interrupt
        self.state = ExitState()

        self._ctrl_c_handler = DoublePressHandler(
            set_pending=lambda p: self._set_state(p, "Ctrl-C"),
            on_double_press=self._exit_fn,
        )
        self._ctrl_d_handler = DoublePressHandler(
            set_pending=lambda p: self._set_state(p, "Ctrl-D"),
            on_double_press=self._exit_fn,
        )

    def _set_state(self, pending: bool, key_name: str) -> None:
        self.state = ExitState(pending=pending, key_name=key_name if pending else None)

    def handle_interrupt(self) -> None:
        """Handle ctrl+c / app:interrupt."""
        if self._on_interrupt and self._on_interrupt():
            return  # Feature handled it
        self._ctrl_c_handler.press()

    def handle_exit(self) -> None:
        """Handle ctrl+d / app:exit."""
        self._ctrl_d_handler.press()

    def reset(self) -> None:
        """Reset all state."""
        self._ctrl_c_handler.reset()
        self._ctrl_d_handler.reset()
        self.state = ExitState()
