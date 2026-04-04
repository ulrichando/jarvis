"""
Auto-dream configuration.

Leaf config module -- intentionally minimal imports so UI components
can read the auto-dream enabled state without heavy dependencies.
"""

from __future__ import annotations

import os


def is_auto_dream_enabled() -> bool:
    """Whether background memory consolidation should run.

    Controlled via JARVIS_AUTO_DREAM environment variable or settings.
    """
    env_val = os.environ.get("JARVIS_AUTO_DREAM")
    if env_val is not None:
        return env_val.lower() in ("1", "true", "yes")
    return False
