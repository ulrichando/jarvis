"""Convenience wrapper that wires up ExitOnCtrlCD with keybindings.

This is the standard way to use ExitOnCtrlCD in components.
"""

from __future__ import annotations

from typing import Callable, Optional

from .use_exit_on_ctrl_cd import ExitOnCtrlCD, ExitState


def create_exit_handler(
    exit_fn: Callable[[], None],
    on_interrupt: Optional[Callable[[], bool]] = None,
) -> ExitOnCtrlCD:
    """Create an exit handler wired with keybindings.

    Equivalent to useExitOnCtrlCDWithKeybindings React hook.

    Args:
        exit_fn: Function to call when exiting.
        on_interrupt: Optional callback for features to handle interrupt (ctrl+c).
                     Return True if handled.

    Returns:
        ExitOnCtrlCD instance.
    """
    return ExitOnCtrlCD(exit_fn=exit_fn, on_interrupt=on_interrupt)
