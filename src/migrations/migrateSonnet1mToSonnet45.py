"""Migration: Migrate Sonnet 1M to Sonnet 4.5."""

from __future__ import annotations


def migrate(config: dict) -> dict:
    """Update model references from Sonnet 1M to Sonnet 4.5."""
    model = config.get("model")
    if model and "sonnet-1m" in str(model):
        config["model"] = str(model).replace("sonnet-1m", "sonnet-4-5")
    return config
