"""Bundled mode detection utilities."""

from __future__ import annotations

import sys


def is_running_with_bun() -> bool:
    """Detect if the current runtime is Bun. Always False for Python."""
    return False


def is_in_bundled_mode() -> bool:
    """Detect if running as a bundled/frozen executable."""
    return getattr(sys, "frozen", False)
