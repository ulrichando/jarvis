"""Cancel request handling for active tasks and command queues."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional

KILL_AGENTS_CONFIRM_WINDOW_MS = 3000


@dataclass
class CancelRequestHandlerProps:
    on_cancel: Callable[[], None]
    on_agents_killed: Callable[[], None]
    abort_signal: Optional[Any] = None
    pop_command_from_queue: Optional[Callable[[], None]] = None
    is_message_selector_visible: bool = False
    is_searching_history: bool = False
    is_help_open: bool = False
    is_overlay_active: bool = False
    is_local_jsx_command: bool = False
    stream_mode: Optional[str] = None


class CancelRequestHandler:
    """Handles cancel/escape/interrupt requests.

    Manages priority-based cancellation:
    1. Active task cancellation
    2. Command queue popping
    3. Fallback cancel

    Equivalent to CancelRequestHandler React component.
    """

    def __init__(self, props: CancelRequestHandlerProps):
        self.props = props
        self._last_kill_agents_press: float = 0

    @property
    def can_cancel_running_task(self) -> bool:
        signal = self.props.abort_signal
        if signal is None:
            return False
        return not getattr(signal, "aborted", True)

    def handle_cancel(self) -> None:
        """Handle a cancel request with priority-based logic."""
        # Priority 1: Cancel active task
        if self.can_cancel_running_task:
            self.props.on_cancel()
            return

        # Priority 2: Pop from command queue
        if self.props.pop_command_from_queue:
            self.props.pop_command_from_queue()
            return

        # Fallback
        self.props.on_cancel()

    def handle_interrupt(self) -> None:
        """Handle Ctrl+C interrupt."""
        if self.can_cancel_running_task:
            self.handle_cancel()

    def handle_kill_agents(
        self,
        has_running_agents: bool,
        kill_all_fn: Callable[[], bool],
        add_notification: Callable,
        remove_notification: Callable,
    ) -> None:
        """Handle kill-agents with two-press confirmation pattern."""
        if not has_running_agents:
            add_notification(
                key="kill-agents-none",
                text="No background agents running",
                timeout_ms=2000,
            )
            return

        now = time.time() * 1000
        elapsed = now - self._last_kill_agents_press

        if elapsed <= KILL_AGENTS_CONFIRM_WINDOW_MS:
            # Second press within window -- kill all
            self._last_kill_agents_press = 0
            remove_notification("kill-agents-confirm")
            kill_all_fn()
            return

        # First press -- show confirmation
        self._last_kill_agents_press = now
        add_notification(
            key="kill-agents-confirm",
            text="Press again to stop background agents",
            timeout_ms=KILL_AGENTS_CONFIRM_WINDOW_MS,
        )
