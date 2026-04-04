"""Desktop notification after idle timeout."""

from __future__ import annotations

import os
import time
from typing import Callable, Optional

DEFAULT_INTERACTION_THRESHOLD_MS = 6000

_last_interaction_time: float = 0


def update_last_interaction_time(force: bool = False) -> None:
    """Update the last interaction timestamp."""
    global _last_interaction_time
    _last_interaction_time = time.time() * 1000


def get_last_interaction_time() -> float:
    return _last_interaction_time


def get_time_since_last_interaction() -> float:
    return time.time() * 1000 - _last_interaction_time


def has_recent_interaction(threshold: float = DEFAULT_INTERACTION_THRESHOLD_MS) -> bool:
    return get_time_since_last_interaction() < threshold


def should_notify(threshold: float = DEFAULT_INTERACTION_THRESHOLD_MS) -> bool:
    return os.environ.get("NODE_ENV") != "test" and not has_recent_interaction(threshold)


class NotifyAfterTimeout:
    """Manages desktop notifications after a timeout period.

    Shows a notification in two cases:
    1. Immediately if the app has been idle for longer than the threshold
    2. After the specified timeout if the user doesn't interact within that time

    Equivalent to useNotifyAfterTimeout React hook.
    """

    def __init__(
        self,
        message: str,
        notification_type: str,
        send_notification: Optional[Callable] = None,
        threshold_ms: float = DEFAULT_INTERACTION_THRESHOLD_MS,
    ):
        self.message = message
        self.notification_type = notification_type
        self.send_notification = send_notification
        self.threshold_ms = threshold_ms
        self._has_notified = False

        # Reset interaction time
        update_last_interaction_time(force=True)

    def check_and_notify(self) -> bool:
        """Check if notification should be sent. Returns True if sent."""
        if self._has_notified:
            return False

        if should_notify(self.threshold_ms):
            self._has_notified = True
            if self.send_notification:
                self.send_notification(
                    message=self.message,
                    notification_type=self.notification_type,
                )
            return True
        return False

    def reset(self) -> None:
        """Reset notification state."""
        self._has_notified = False
        update_last_interaction_time(force=True)
