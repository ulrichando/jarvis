"""Plugin options flow."""

from __future__ import annotations

from typing import Any


async def run_plugin_options_flow(plugin_name: str, **_kwargs: Any) -> dict[str, Any]:
    """Run the plugin options configuration flow."""
    return {"plugin": plugin_name, "configured": True}
