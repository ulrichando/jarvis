"""
Migration: Move bypassPermissionsModeAccepted from global config to settings.json
as skipDangerousModePermissionPrompt. This is a better home since settings.json
is the user-configurable settings file.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def get_global_config() -> dict[str, Any]:
    """Placeholder: load global config."""
    return {}


def save_global_config(updater: Any) -> None:
    """Placeholder: save global config."""
    pass


def has_skip_dangerous_mode_permission_prompt() -> bool:
    """Placeholder: check if skipDangerousModePermissionPrompt is set."""
    return False


def update_settings_for_source(source: str, updates: dict[str, Any]) -> None:
    """Placeholder: update settings for a given source."""
    pass


def log_event(event_name: str, metadata: dict[str, Any]) -> None:
    """Placeholder: log an analytics event."""
    pass


def migrate_bypass_permissions_accepted_to_settings() -> None:
    """
    Migration: Move bypassPermissionsModeAccepted from global config to settings.json
    as skipDangerousModePermissionPrompt.
    """
    global_config = get_global_config()

    if not global_config.get("bypassPermissionsModeAccepted"):
        return

    try:
        if not has_skip_dangerous_mode_permission_prompt():
            update_settings_for_source(
                "userSettings",
                {"skipDangerousModePermissionPrompt": True},
            )

        log_event("tengu_migrate_bypass_permissions_accepted", {})

        def update_config(current: dict[str, Any]) -> dict[str, Any]:
            if "bypassPermissionsModeAccepted" not in current:
                return current
            return {k: v for k, v in current.items()
                    if k != "bypassPermissionsModeAccepted"}

        save_global_config(update_config)

    except Exception as error:
        logger.error(
            f"Failed to migrate bypass permissions accepted: {error}"
        )
