"""
Initialize error log and analytics sinks.

Leaf module -- kept separate to avoid import cycles.
"""

from __future__ import annotations

_initialized = False


def init_sinks() -> None:
    """
    Attach error log and analytics sinks.
    Idempotent -- safe to call multiple times.
    """
    global _initialized
    if _initialized:
        return
    _initialized = True
    # In the Python version, error logging and analytics are handled
    # by the Brain's own logging infrastructure. This is a placeholder
    # for any additional sink initialization.
