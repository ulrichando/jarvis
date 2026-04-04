"""LSP initialization notification."""

from __future__ import annotations

from typing import Callable, Optional


def check_lsp_initialization(
    add_notification: Optional[Callable] = None,
    lsp_status: Optional[str] = None,
) -> None:
    """Show LSP initialization status.

    Equivalent to useLspInitializationNotification React hook.
    """
    if not add_notification or not lsp_status:
        return
    if lsp_status == "initializing":
        add_notification(
            key="lsp-init",
            text="LSP server initializing...",
            priority="low",
        )
    elif lsp_status == "ready":
        add_notification(
            key="lsp-init",
            text="LSP server ready",
            priority="low",
            timeout_ms=3000,
        )
