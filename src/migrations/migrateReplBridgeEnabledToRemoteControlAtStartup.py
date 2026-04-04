"""
Migrate the `replBridgeEnabled` config key to `remoteControlAtStartup`.

The old key was an implementation detail that leaked into user-facing config.
This migration copies the value to the new key and removes the old one.
Idempotent -- only acts when the old key exists and the new one doesn't.
"""

from __future__ import annotations

from typing import Any


def save_global_config(updater: Any) -> None:
    """Placeholder: save global config."""
    pass


def migrate_repl_bridge_enabled_to_remote_control_at_startup() -> None:
    """
    Migrate the replBridgeEnabled config key to remoteControlAtStartup.
    """

    def update_config(prev: dict[str, Any]) -> dict[str, Any]:
        old_value = prev.get("replBridgeEnabled")
        if old_value is None:
            return prev
        if prev.get("remoteControlAtStartup") is not None:
            return prev
        next_config = {**prev, "remoteControlAtStartup": bool(old_value)}
        next_config.pop("replBridgeEnabled", None)
        return next_config

    save_global_config(update_config)
