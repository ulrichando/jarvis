"""
Migration: Move MCP server approval fields from project config to local settings.
This migrates both enableAllProjectMcpServers and enabledMcpjsonServers to the
settings system for better management and consistency.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def get_current_project_config() -> dict[str, Any]:
    """Placeholder: load current project config."""
    return {}


def save_current_project_config(updater: Any) -> None:
    """Placeholder: save current project config."""
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


def migrate_enable_all_project_mcp_servers_to_settings() -> None:
    """
    Migration: Move MCP server approval fields from project config to local settings.
    """
    project_config = get_current_project_config()

    has_enable_all = project_config.get("enableAllProjectMcpServers") is not None
    enabled_servers = project_config.get("enabledMcpjsonServers", [])
    disabled_servers = project_config.get("disabledMcpjsonServers", [])
    has_enabled_servers = bool(enabled_servers)
    has_disabled_servers = bool(disabled_servers)

    if not has_enable_all and not has_enabled_servers and not has_disabled_servers:
        return

    try:
        existing_settings = get_settings_for_source("localSettings") or {}
        updates: dict[str, Any] = {}
        fields_to_remove: list[str] = []

        # Migrate enableAllProjectMcpServers if it exists and hasn't been migrated
        if has_enable_all:
            if existing_settings.get("enableAllProjectMcpServers") is None:
                updates["enableAllProjectMcpServers"] = project_config[
                    "enableAllProjectMcpServers"
                ]
            fields_to_remove.append("enableAllProjectMcpServers")

        # Migrate enabledMcpjsonServers if it exists
        if has_enabled_servers:
            existing_enabled = existing_settings.get("enabledMcpjsonServers", [])
            # Merge servers (avoiding duplicates)
            updates["enabledMcpjsonServers"] = list(
                set(existing_enabled) | set(enabled_servers)
            )
            fields_to_remove.append("enabledMcpjsonServers")

        # Migrate disabledMcpjsonServers if it exists
        if has_disabled_servers:
            existing_disabled = existing_settings.get("disabledMcpjsonServers", [])
            updates["disabledMcpjsonServers"] = list(
                set(existing_disabled) | set(disabled_servers)
            )
            fields_to_remove.append("disabledMcpjsonServers")

        # Update settings if there are any updates
        if updates:
            update_settings_for_source("localSettings", updates)

        # Remove migrated fields from project config
        if fields_to_remove:
            keys_to_remove = {
                "enableAllProjectMcpServers",
                "enabledMcpjsonServers",
                "disabledMcpjsonServers",
            }

            def update_config(current: dict[str, Any]) -> dict[str, Any]:
                return {k: v for k, v in current.items() if k not in keys_to_remove}

            save_current_project_config(update_config)

        log_event(
            "tengu_migrate_mcp_approval_fields_success",
            {"migratedCount": len(fields_to_remove)},
        )

    except Exception as e:
        logger.error(str(e))
        log_event("tengu_migrate_mcp_approval_fields_error", {})
