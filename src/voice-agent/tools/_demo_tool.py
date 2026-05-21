"""TEMPORARY validation tool — DELETE once real tools are ported.

Exists ONLY to exercise end-to-end discovery + adaptation:
``discover_builtin_tools()`` must AST-find this module's module-level
``registry.register(...)`` call, import it, and ``load_all_livekit_tools()``
must return ``echo_demo`` as a working ``RawFunctionTool``.

It is NOT a real JARVIS tool and carries no production behavior. The first
real Hermes tool port should replace this file's purpose; remove it then.
"""
from __future__ import annotations

from .registry import registry


def _echo_demo(raw_arguments: dict) -> str:
    """Return the ``text`` argument verbatim (the canonical no-op tool)."""
    return str(raw_arguments.get("text", ""))


registry.register(
    name="echo_demo",
    schema={
        "description": "TEMP validation tool: echoes the `text` argument back. Not for production use.",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to echo back."},
            },
            "required": ["text"],
        },
    },
    handler=_echo_demo,
    is_async=False,
    description="TEMP validation tool: echoes the `text` argument back. Not for production use.",
    emoji="🔁",
)
