"""Configuration management utilities."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

ReleaseChannel = str  # 'stable' | 'latest'


@dataclass
class PastedContent:
    id: int
    type: str  # 'text' | 'image'
    content: str
    media_type: Optional[str] = None
    filename: Optional[str] = None


@dataclass
class HistoryEntry:
    display: str
    pasted_contents: dict[int, PastedContent] = field(default_factory=dict)


@dataclass
class AccountInfo:
    organization_role: Optional[str] = None
    workspace_role: Optional[str] = None


@dataclass
class ProjectConfig:
    allowed_tools: list[str] = field(default_factory=list)
    mcp_context_uris: list[str] = field(default_factory=list)
    mcp_servers: dict[str, Any] = field(default_factory=dict)


def _get_config_home() -> str:
    return os.environ.get(
        "JARVIS_HOME",
        os.path.join(str(Path.home()), ".jarvis")
    )


def _get_config_path() -> str:
    return os.path.join(_get_config_home(), "config.json")


def get_global_config() -> dict[str, Any]:
    """Read the global configuration file."""
    try:
        with open(_get_config_path()) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_global_config(updater=None) -> None:
    """Save the global configuration file."""
    config = get_global_config()
    if updater:
        config = updater(config)
    os.makedirs(_get_config_home(), exist_ok=True)
    with open(_get_config_path(), "w") as f:
        json.dump(config, f, indent=2)


def get_current_project_config() -> ProjectConfig:
    """Get the current project configuration."""
    config_path = os.path.join(os.getcwd(), ".jarvis", "settings.json")
    try:
        with open(config_path) as f:
            data = json.load(f)
        return ProjectConfig(
            allowed_tools=data.get("allowedTools", []),
            mcp_context_uris=data.get("mcpContextUris", []),
            mcp_servers=data.get("mcpServers", {}),
        )
    except (FileNotFoundError, json.JSONDecodeError):
        return ProjectConfig()


def check_has_trust_dialog_accepted() -> bool:
    """Check if trust dialog has been accepted for current directory."""
    config = get_global_config()
    trusted = config.get("trustedDirectories", [])
    return os.getcwd() in trusted


def get_memory_path() -> str:
    """Get the path to the memory directory."""
    return os.path.join(_get_config_home(), "memory")


def get_managed_rules_dir() -> Optional[str]:
    """Get the managed rules directory."""
    path = "/etc/jarvis/rules"
    return path if os.path.isdir(path) else None


def get_user_rules_dir() -> str:
    """Get the user rules directory."""
    return os.path.join(_get_config_home(), "rules")
