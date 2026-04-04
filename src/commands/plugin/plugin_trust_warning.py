"""Plugin trust warning display."""

from __future__ import annotations

from typing import Any


def show_trust_warning(plugin_name: str, source: str) -> str:
    """Generate a trust warning message for a plugin."""
    return (
        f"Warning: Plugin '{plugin_name}' from '{source}' has not been verified. "
        f"Only install plugins from sources you trust."
    )
