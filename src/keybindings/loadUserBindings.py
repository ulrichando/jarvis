"""Load user keybindings from configuration files."""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

from .types import KeybindingBlock

logger = logging.getLogger(__name__)


def load_user_bindings(config_dir: Optional[str] = None) -> list[KeybindingBlock]:
    """Load user keybindings from ~/.jarvis/keybindings.json."""
    home = config_dir or os.environ.get("JARVIS_HOME", os.path.expanduser("~/.jarvis"))
    path = os.path.join(home, "keybindings.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path) as f:
            data = json.load(f)
        if not isinstance(data, list):
            return []
        result = []
        for block in data:
            if isinstance(block, dict) and "context" in block and "bindings" in block:
                result.append(KeybindingBlock(
                    context=block["context"],
                    bindings=block.get("bindings", {}),
                ))
        return result
    except Exception as err:
        logger.warning("Failed to load keybindings: %s", err)
        return []
