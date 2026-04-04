"""Plugin options dialog."""

from __future__ import annotations

from typing import Any


async def show_plugin_options(plugin_name: str, **_kwargs: Any) -> dict[str, Any]:
    """Show options dialog for a plugin."""
    return {"plugin": plugin_name, "options": {}}
