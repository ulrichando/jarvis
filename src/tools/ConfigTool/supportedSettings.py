"""Supported configuration settings registry."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class SettingConfig:
    description: str
    type: str = "string"  # "string", "boolean", "number"
    source: str = "global"  # "global" or "project"
    options: Optional[list[str]] = None


SUPPORTED_SETTINGS: dict[str, SettingConfig] = {
    "theme": SettingConfig(
        description="Visual theme for the interface",
        options=["dark", "light", "light-daltonized", "dark-daltonized"],
    ),
    "verbose": SettingConfig(
        description="Show detailed output",
        type="boolean",
        source="project",
    ),
    "editorMode": SettingConfig(
        description="Editor key bindings",
        options=["normal", "vim", "emacs"],
        source="project",
    ),
    "model": SettingConfig(
        description="Override the default model",
    ),
    "permissions.defaultMode": SettingConfig(
        description="Default permission mode",
        options=["ask", "acceptEdits", "bypassPermissions", "plan"],
    ),
}


def get_options_for_setting(key: str) -> Optional[list[str]]:
    """Get the valid options for a setting."""
    config = SUPPORTED_SETTINGS.get(key)
    if config:
        return config.options
    return None
