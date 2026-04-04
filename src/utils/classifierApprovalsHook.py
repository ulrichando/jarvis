"""Hook for classifier approvals store (non-React version)."""

from __future__ import annotations

from .classifierApprovals import is_classifier_checking, subscribe_classifier_checking


def use_is_classifier_checking(tool_use_id: str) -> bool:
    """Check if a classifier is currently checking a tool use."""
    return is_classifier_checking(tool_use_id)
