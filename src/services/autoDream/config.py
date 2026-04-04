"""
Auto-dream configuration.

Leaf config module -- intentionally minimal imports so UI components
can read the auto-dream enabled state without heavy dependencies.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


def is_auto_dream_enabled() -> bool:
    """Whether background memory consolidation should run.

    Checked in order:
      1. JARVIS_AUTO_DREAM env var (explicit override)
      2. ``auto_dream`` key in ~/.jarvis/settings.json
      3. Default: **True** (enabled out of the box)
    """
    # 1. Env var wins
    env_val = os.environ.get("JARVIS_AUTO_DREAM")
    if env_val is not None:
        return env_val.lower() in ("1", "true", "yes")

    # 2. Settings file
    settings_path = Path(
        os.environ.get("JARVIS_HOME", os.path.expanduser("~/.jarvis"))
    ) / "settings.json"
    try:
        if settings_path.is_file():
            data = json.loads(settings_path.read_text(encoding="utf-8"))
            if "auto_dream" in data:
                return bool(data["auto_dream"])
    except (json.JSONDecodeError, OSError):
        pass

    # 3. Default on
    return True
