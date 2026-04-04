"""Branch command implementation."""

from __future__ import annotations

import re
import uuid
from typing import Any, Optional


def derive_first_prompt(first_user_message: Optional[dict] = None) -> str:
    """Derive a single-line title base from the first user message."""
    if not first_user_message:
        return "Branched conversation"
    content = first_user_message.get("message", {}).get("content")
    if not content:
        return "Branched conversation"
    if isinstance(content, str):
        raw = content
    elif isinstance(content, list):
        raw = next(
            (block.get("text", "") for block in content if block.get("type") == "text"),
            "",
        )
    else:
        return "Branched conversation"
    return re.sub(r"\s+", " ", raw).strip()[:100] or "Branched conversation"


async def call(
    on_done: Any = None,
    context: Any = None,
    args: str = "",
    **_kwargs: Any,
) -> None:
    """Create a fork of the current conversation."""
    custom_title = args.strip() if args else None
    fork_session_id = str(uuid.uuid4())

    try:
        title_info = f' "{custom_title}"' if custom_title else ""
        message = f"Branched conversation{title_info}. New session: {fork_session_id}"
        if on_done:
            on_done(message, {"display": "system"})
    except Exception as e:
        error_msg = str(e) if str(e) else "Unknown error occurred"
        if on_done:
            on_done(f"Failed to branch conversation: {error_msg}")
