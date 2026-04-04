"""Agents command implementation."""

from __future__ import annotations

from typing import Any


async def call(on_done: Any = None, context: Any = None, **_kwargs: Any) -> None:
    """Manage agent configurations."""
    if context:
        app_state = context.get_app_state() if hasattr(context, "get_app_state") else {}
        # List available agent configurations
        agents = app_state.get("agents", [])
        result = f"Available agents: {len(agents)}"
        if on_done:
            on_done(result)
    elif on_done:
        on_done("No context available.")
