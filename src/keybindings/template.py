"""Keybinding template generation for user configuration."""

from __future__ import annotations

import json
from .defaultBindings import DEFAULT_BINDINGS


def generate_template() -> str:
    """Generate a keybindings.json template with defaults."""
    blocks = []
    for block in DEFAULT_BINDINGS:
        blocks.append({
            "context": block.context,
            "bindings": block.bindings,
        })
    return json.dumps(blocks, indent=2)
