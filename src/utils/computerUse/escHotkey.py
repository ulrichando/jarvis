"""ESC hotkey handling for computer use."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def register_esc_hotkey(callback: callable) -> callable:
    """Register an ESC hotkey handler. Returns unregister function."""
    logger.debug("ESC hotkey registered")

    def unregister() -> None:
        logger.debug("ESC hotkey unregistered")

    return unregister
