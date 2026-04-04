"""Compact command - Clear conversation history but keep a summary."""

from __future__ import annotations

import os

command = {
    "type": "local",
    "name": "compact",
    "description": "Clear conversation history but keep a summary in context. "
    "Optional: /compact [instructions for summarization]",
    "is_enabled": lambda: not os.environ.get("DISABLE_COMPACT", "").lower() in ("1", "true", "yes"),
    "supports_non_interactive": True,
    "argument_hint": "<optional custom summarization instructions>",
}
