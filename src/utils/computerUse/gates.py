"""Computer use feature gates."""

from __future__ import annotations

import os
import platform


def is_computer_use_available() -> bool:
    """Check if computer use is available on this platform."""
    return platform.system() == "Darwin"


def is_computer_use_enabled() -> bool:
    """Check if computer use is enabled via configuration."""
    return (
        is_computer_use_available()
        and os.environ.get("CLAUDE_CODE_DISABLE_COMPUTER_USE") != "1"
    )
