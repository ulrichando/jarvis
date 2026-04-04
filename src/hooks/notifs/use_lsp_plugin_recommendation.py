"""LSP plugin recommendation notification."""

from __future__ import annotations

from typing import Callable, Optional


def check_lsp_plugin_recommendation(
    add_notification: Optional[Callable] = None,
    has_lsp_support: bool = False,
) -> None:
    """Show recommendation for LSP plugin.

    Equivalent to useLspPluginRecommendation React hook.
    """
    pass  # LSP plugin recommendation logic
