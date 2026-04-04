"""
Notification service.

Sends notifications through various channels (terminal bell, iTerm2, etc).
"""

from __future__ import annotations

import logging
import os
import platform
import random
import subprocess
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)

DEFAULT_TITLE = "JARVIS"


@dataclass
class NotificationOptions:
    message: str
    title: Optional[str] = None
    notification_type: str = ""


async def send_notification(notif: NotificationOptions) -> str:
    """Send a notification through the configured channel.

    Returns the method used to send the notification.
    """
    # Fire Notification hooks
    try:
        from src.hooks import HooksManager
        if not hasattr(send_notification, "_hooks"):
            send_notification._hooks = HooksManager()
            send_notification._hooks.load()
        send_notification._hooks.run_notification(notif.message, notif.notification_type)
    except Exception:
        pass  # Hooks are best-effort

    channel = os.environ.get("JARVIS_NOTIFICATION_CHANNEL", "auto")
    return await _send_to_channel(channel, notif)


async def _send_to_channel(channel: str, opts: NotificationOptions) -> str:
    """Send notification via the specified channel."""
    title = opts.title or DEFAULT_TITLE

    try:
        if channel == "auto":
            return await _send_auto(opts)
        elif channel == "terminal_bell":
            _notify_bell()
            return "terminal_bell"
        elif channel == "notifications_disabled":
            return "disabled"
        else:
            return "none"
    except Exception:
        return "error"


async def _send_auto(opts: NotificationOptions) -> str:
    """Auto-detect and send notification."""
    term = os.environ.get("TERM_PROGRAM", "")

    if term == "iTerm.app":
        _notify_iterm2(opts)
        return "iterm2"

    # Default: try terminal bell
    _notify_bell()
    return "terminal_bell"


def _notify_bell() -> None:
    """Send a terminal bell character."""
    print("\a", end="", flush=True)


def _notify_iterm2(opts: NotificationOptions) -> None:
    """Send an iTerm2 notification via escape sequence."""
    title = opts.title or DEFAULT_TITLE
    message = opts.message
    # iTerm2 proprietary escape sequence for notifications
    print(f"\033]9;{title}: {message}\007", end="", flush=True)


def _generate_kitty_id() -> int:
    return random.randint(0, 10000)
