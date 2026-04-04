"""Billing access utilities."""

from __future__ import annotations

from typing import Optional

# Mock billing access for testing
_mock_billing_access_override: Optional[bool] = None


def has_console_billing_access() -> bool:
    """Check if user has console billing access."""
    import os

    if os.environ.get("DISABLE_COST_WARNINGS", "").lower() in ("1", "true", "yes"):
        return False
    # Simplified: check for org/workspace roles
    return False


def set_mock_billing_access_override(value: Optional[bool]) -> None:
    """Set mock billing access for testing."""
    global _mock_billing_access_override
    _mock_billing_access_override = value


def has_claude_ai_billing_access() -> bool:
    """Check if user has Claude AI billing access."""
    if _mock_billing_access_override is not None:
        return _mock_billing_access_override
    return False
