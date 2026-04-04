"""
Migration: Move user-set autoUpdates preference to settings.json env var.
Only migrates if user explicitly disabled auto-updates (not for protection).
This preserves user intent while allowing native installations to auto-update.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def get_global_config() -> dict[str, Any]:
    """Placeholder: load global config from ~/.jarvis/config.json."""
    return {}


def save_global_config(updater: Any) -> None:
    """Placeholder: save global config."""
    pass


def get_settings_for_source(source: str) -> dict[str, Any] | None:
    """Placeholder: get settings for a given source."""
    return None


def update_settings_for_source(source: str, updates: dict[str, Any]) -> None:
    """Placeholder: update settings for a given source."""
    pass


def log_event(event_name: str, metadata: dict[str, Any]) -> None:
    """Placeholder: log an analytics event."""
    pass


def migrate_auto_updates_to_settings() -> None:
    """
    Migration: Move user-set autoUpdates preference to settings.json env var.
    Only migrates if user explicitly disabled auto-updates (not for protection).
    This preserves user intent while allowing native installations to auto-update.
    """
    global_config = get_global_config()

    # Only migrate if autoUpdates was explicitly set to false by user preference
    # (not automatically for native protection)
    if (
        global_config.get("autoUpdates") is not False
        or global_config.get("autoUpdatesProtectedForNative") is True
    ):
        return

    try:
        user_settings = get_settings_for_source("userSettings") or {}

        # Always set DISABLE_AUTOUPDATER to preserve user intent
        update_settings_for_source(
            "userSettings",
            {
                **user_settings,
                "env": {
                    **user_settings.get("env", {}),
                    "DISABLE_AUTOUPDATER": "1",
                },
            },
        )

        log_event(
            "tengu_migrate_autoupdates_to_settings",
            {
                "was_user_preference": True,
                "already_had_env_var": bool(
                    user_settings.get("env", {}).get("DISABLE_AUTOUPDATER")
                ),
            },
        )

        # Explicitly set so this takes effect immediately
        os.environ["DISABLE_AUTOUPDATER"] = "1"

        # Remove autoUpdates from global config after successful migration
        def update_config(current: dict[str, Any]) -> dict[str, Any]:
            updated = {k: v for k, v in current.items()
                       if k not in ("autoUpdates", "autoUpdatesProtectedForNative")}
            return updated

        save_global_config(update_config)

    except Exception as error:
        logger.error(f"Failed to migrate auto-updates: {error}")
        log_event("tengu_migrate_autoupdates_error", {"has_error": True})
