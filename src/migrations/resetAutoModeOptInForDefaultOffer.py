"""Migration: Reset auto mode opt-in for default offer."""

from __future__ import annotations


def migrate(config: dict) -> dict:
    """Reset auto mode opt-in setting."""
    config.pop("autoModeOptIn", None)
    return config
