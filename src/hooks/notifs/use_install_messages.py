"""Installation-related notification messages."""

from __future__ import annotations

from typing import Callable, List, Optional


def check_install_messages(
    add_notification: Optional[Callable] = None,
    messages: Optional[List[str]] = None,
) -> None:
    """Show installation-related messages.

    Equivalent to useInstallMessages React hook.
    """
    if not messages or not add_notification:
        return
    for i, msg in enumerate(messages):
        add_notification(
            key=f"install-msg-{i}",
            text=msg,
            priority="low",
        )
