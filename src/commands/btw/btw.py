"""BTW command implementation - side question handler."""

from __future__ import annotations

from typing import Any


async def call(on_done: Any = None, context: Any = None, args: str = "", **_kwargs: Any) -> None:
    """Handle a side question without interrupting the main conversation."""
    question = args.strip() if args else ""
    if not question:
        if on_done:
            on_done("Please provide a question. Usage: /btw <question>")
        return
    # In the original, this spawns a side agent to answer the question
    if on_done:
        on_done(f"Side question received: {question}")
