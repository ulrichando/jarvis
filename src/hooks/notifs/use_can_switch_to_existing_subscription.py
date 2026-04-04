"""Notification for switching to existing subscription."""

from __future__ import annotations

from typing import Callable, Optional


def check_can_switch_subscription(
    add_notification: Optional[Callable] = None,
    has_existing_subscription: bool = False,
) -> None:
    """Show notification about switching to existing subscription.

    Equivalent to useCanSwitchToExistingSubscription React hook.
    """
    if not has_existing_subscription or not add_notification:
        return
    add_notification(
        key="switch-subscription",
        text="You have an existing subscription you can switch to",
        priority="low",
    )
