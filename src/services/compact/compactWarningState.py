"""
Compact warning state management.

Tracks whether the "context left until autocompact" warning should be suppressed.
"""

from __future__ import annotations

_compact_warning_suppressed = False


def suppress_compact_warning() -> None:
    """Suppress the compact warning. Call after successful compaction."""
    global _compact_warning_suppressed
    _compact_warning_suppressed = True


def clear_compact_warning_suppression() -> None:
    """Clear the compact warning suppression."""
    global _compact_warning_suppressed
    _compact_warning_suppressed = False


def is_compact_warning_suppressed() -> bool:
    """Check if the compact warning is currently suppressed."""
    return _compact_warning_suppressed
