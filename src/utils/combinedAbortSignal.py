"""
Combined abort signal that aborts when any input signal aborts
or an optional timeout elapses.
"""

from __future__ import annotations

import threading
from typing import Callable, Optional

from .abortController import AbortController, AbortSignal, create_abort_controller


def create_combined_abort_signal(
    signal: Optional[AbortSignal] = None,
    signal_b: Optional[AbortSignal] = None,
    timeout_ms: Optional[float] = None,
) -> tuple[AbortSignal, Callable[[], None]]:
    """
    Creates a combined AbortSignal that aborts when the input signal aborts,
    an optional second signal aborts, or an optional timeout elapses.

    Returns:
        Tuple of (signal, cleanup_function).
    """
    combined = create_abort_controller()

    if (signal and signal.aborted) or (signal_b and signal_b.aborted):
        combined.abort()
        return combined.signal, lambda: None

    timer: Optional[threading.Timer] = None

    def abort_combined() -> None:
        nonlocal timer
        if timer is not None:
            timer.cancel()
        combined.abort()

    if timeout_ms is not None:
        timer = threading.Timer(timeout_ms / 1000, abort_combined)
        timer.daemon = True
        timer.start()

    if signal is not None:
        signal.add_listener(abort_combined)
    if signal_b is not None:
        signal_b.add_listener(abort_combined)

    def cleanup() -> None:
        nonlocal timer
        if timer is not None:
            timer.cancel()
        if signal is not None:
            signal.remove_listener(abort_combined)
        if signal_b is not None:
            signal_b.remove_listener(abort_combined)

    return combined.signal, cleanup
