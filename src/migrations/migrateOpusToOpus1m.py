"""
Migrate users with 'opus' pinned in their settings to 'opus[1m]' when they
are eligible for the merged Opus 1M experience (Max/Team Premium on 1P).

CLI invocations with --model opus are unaffected: that flag is a runtime
override and does not touch userSettings, so it continues to use plain Opus.

Idempotent: only writes if userSettings.model is exactly 'opus'.
"""

from __future__ import annotations

from typing import Any


def is_opus_1m_merge_enabled() -> bool:
    """Placeholder: check if opus 1m merge is enabled."""
    return False


def get_default_main_loop_model_setting() -> str:
    """Placeholder: get default main loop model setting."""
    return ""


def parse_user_specified_model(model: str) -> str:
    """Placeholder: parse user specified model."""
    return model


def get_settings_for_source(source: str) -> dict[str, Any] | None:
    """Placeholder: get settings for a given source."""
    return None


def update_settings_for_source(source: str, updates: dict[str, Any]) -> None:
    """Placeholder: update settings for a given source."""
    pass


def log_event(event_name: str, metadata: dict[str, Any]) -> None:
    """Placeholder: log an analytics event."""
    pass


def migrate_opus_to_opus_1m() -> None:
    """
    Migrate users with 'opus' pinned in their settings to 'opus[1m]'.
    """
    if not is_opus_1m_merge_enabled():
        return

    settings = get_settings_for_source("userSettings")
    model = settings.get("model") if settings else None

    if model != "opus":
        return

    migrated = "opus[1m]"
    model_to_set: str | None = migrated
    if parse_user_specified_model(migrated) == parse_user_specified_model(
        get_default_main_loop_model_setting()
    ):
        model_to_set = None

    update_settings_for_source("userSettings", {"model": model_to_set})
    log_event("tengu_opus_to_opus1m_migration", {})
