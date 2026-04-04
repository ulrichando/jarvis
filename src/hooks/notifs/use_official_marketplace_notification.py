"""Official marketplace notification."""

from __future__ import annotations

from typing import Callable, Optional


def check_official_marketplace(
    add_notification: Optional[Callable] = None,
    has_marketplace: bool = False,
) -> None:
    """Show notification about the official marketplace.

    Equivalent to useOfficialMarketplaceNotification React hook.
    """
    pass  # Notification logic specific to Claude.ai marketplace
