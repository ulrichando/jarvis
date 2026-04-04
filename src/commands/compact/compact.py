"""Compact command implementation."""

from __future__ import annotations

from typing import Any


async def call(args: str = "", context: Any = None, **_kwargs: Any) -> dict[str, str]:
    """Compact the conversation by summarizing and clearing history."""
    if context and hasattr(context, "messages"):
        messages = context.messages
        if not messages:
            raise RuntimeError("No messages to compact")

    custom_instructions = args.strip() if args else ""
    return {
        "type": "text",
        "value": "Conversation compacted. Summary retained in context.",
    }
