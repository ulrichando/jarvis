"""
Migrate first-party users off explicit Opus 4.0/4.1 model strings.

The 'opus' alias already resolves to Opus 4.6 for 1P, so anyone still
on an explicit 4.0/4.1 string pinned it in settings before 4.5 launched.
parseUserSpecifiedModel now silently remaps these at runtime anyway --
this migration cleans up the settings file so /model shows the right
thing, and sets a timestamp so the REPL can show a one-time notification.

Only touches userSettings.
"""

from __future__ import annotations

import time
from typing import Any


def get_api_provider() -> str:
    """Placeholder: get the API provider."""
    return "firstParty"


def is_legacy_model_remap_enabled() -> bool:
    """Placeholder: check if legacy model remap is enabled."""
    return False


def get_settings_for_source(source: str) -> dict[str, Any] | None:
    """Placeholder: get settings for a given source."""
    return None


def update_settings_for_source(source: str, updates: dict[str, Any]) -> None:
    """Placeholder: update settings for a given source."""
    pass


def save_global_config(updater: Any) -> None:
    """Placeholder: save global config."""
    pass


def log_event(event_name: str, metadata: dict[str, Any]) -> None:
    """Placeholder: log an analytics event."""
    pass


LEGACY_OPUS_MODELS = {
    "claude-opus-4-20250514",
    "claude-opus-4-1-20250805",
    "claude-opus-4-0",
    "claude-opus-4-1",
}


def migrate_legacy_opus_to_current() -> None:
    """
    Migrate first-party users off explicit Opus 4.0/4.1 model strings
    to the 'opus' alias.
    """
    if get_api_provider() != "firstParty":
        return

    if not is_legacy_model_remap_enabled():
        return

    settings = get_settings_for_source("userSettings")
    model = settings.get("model") if settings else None

    if model not in LEGACY_OPUS_MODELS:
        return

    update_settings_for_source("userSettings", {"model": "opus"})

    def update_config(current: dict[str, Any]) -> dict[str, Any]:
        return {**current, "legacyOpusMigrationTimestamp": int(time.time() * 1000)}

    save_global_config(update_config)
    log_event("tengu_legacy_opus_migration", {"from_model": model})
