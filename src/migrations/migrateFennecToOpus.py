"""
Migrate users on removed fennec model aliases to their new Opus 4.6 aliases.
- fennec-latest -> opus
- fennec-latest[1m] -> opus[1m]
- fennec-fast-latest -> opus[1m] + fast mode
- opus-4-5-fast -> opus + fast mode

Only touches userSettings. Reading and writing the same source keeps this
idempotent without a completion flag.
"""

from __future__ import annotations

import os
from typing import Any


def get_settings_for_source(source: str) -> dict[str, Any] | None:
    """Placeholder: get settings for a given source."""
    return None


def update_settings_for_source(source: str, updates: dict[str, Any]) -> None:
    """Placeholder: update settings for a given source."""
    pass


def migrate_fennec_to_opus() -> None:
    """
    Migrate users on removed fennec model aliases to their new Opus 4.6 aliases.
    Only touches userSettings.
    """
    if os.environ.get("USER_TYPE") != "ant":
        return

    settings = get_settings_for_source("userSettings")
    model = settings.get("model") if settings else None

    if not isinstance(model, str):
        return

    if model.startswith("fennec-latest[1m]"):
        update_settings_for_source("userSettings", {"model": "opus[1m]"})
    elif model.startswith("fennec-latest"):
        update_settings_for_source("userSettings", {"model": "opus"})
    elif model.startswith("fennec-fast-latest") or model.startswith("opus-4-5-fast"):
        update_settings_for_source(
            "userSettings", {"model": "opus[1m]", "fastMode": True}
        )
