"""Migration: Migrate Sonnet 4.5 to Sonnet 4.6."""

from __future__ import annotations


def migrate(config: dict) -> dict:
    """Update model references from Sonnet 4.5 to Sonnet 4.6."""
    model = config.get("model")
    if model and "sonnet-4-5" in str(model):
        config["model"] = str(model).replace("sonnet-4-5", "sonnet-4-6")
    return config
