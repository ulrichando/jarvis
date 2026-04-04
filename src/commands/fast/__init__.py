"""Fast command - Toggle fast mode."""

from __future__ import annotations

FAST_MODE_MODEL_DISPLAY = "fast model"

command = {
    "type": "local",
    "name": "fast",
    "description": f"Toggle fast mode ({FAST_MODE_MODEL_DISPLAY} only)",
    "availability": ["claude-ai", "console"],
    "argument_hint": "[on|off]",
}
