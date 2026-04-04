"""Hook for displaying compact warnings to the user."""

from __future__ import annotations

from .compactWarningState import is_compact_warning_suppressed


def should_show_compact_warning(
    current_tokens: int,
    max_tokens: int,
    warning_threshold: float = 0.7,
) -> bool:
    """Check if the compact warning should be shown."""
    if is_compact_warning_suppressed():
        return False
    if max_tokens <= 0:
        return False
    return (current_tokens / max_tokens) >= warning_threshold
