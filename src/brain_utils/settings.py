"""Settings manager for JARVIS -- layered config from user and project sources.

Loads settings from:
  1. ~/.jarvis/settings.json   (user scope, lower priority)
  2. .jarvis/settings.json     (project scope, higher priority)

Project settings override user settings for the same key.

Adapted from TypeScript Settings components.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger("jarvis.settings")

# Common settings keys and their descriptions
KNOWN_SETTINGS: dict[str, str] = {
    "model": "Default LLM model name",
    "effort": "Reasoning effort level (low/medium/high)",
    "permission_mode": "Permission mode (default/plan/trust/deny)",
    "max_iterations": "Maximum agent loop iterations",
    "theme": "UI color theme",
    "hooks_enabled": "Whether pre/post hooks run on tool calls",
}


class SettingsManager:
    """Layered settings manager with user and project scopes.

    Project-scope settings override user-scope settings for the same key.
    Changes are written back to the appropriate JSON file on save().
    """

    def __init__(self, config_dir: str | Path | None = None):
        """Initialize from user and project config directories.

        Args:
            config_dir: Project directory containing .jarvis/settings.json.
                        If None, only user settings are loaded.
        """
        from src.config import JARVIS_HOME

        self._user_path = JARVIS_HOME / "settings.json"
        self._project_path: Path | None = None

        if config_dir is not None:
            self._project_path = Path(config_dir) / ".jarvis" / "settings.json"

        self._user_data: dict[str, Any] = {}
        self._project_data: dict[str, Any] = {}

        self._load()

    # ------------------------------------------------------------------
    # Loading / saving
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """Load settings from both sources."""
        self._user_data = self._read_json(self._user_path)
        if self._project_path:
            self._project_data = self._read_json(self._project_path)

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        """Read a JSON file, returning empty dict on any error."""
        if not path.is_file():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
            log.warning("Settings file %s is not a JSON object, ignoring", path)
            return {}
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Failed to read settings from %s: %s", path, exc)
            return {}

    def save(self) -> None:
        """Persist current settings to disk (both scopes)."""
        self._write_json(self._user_path, self._user_data)
        if self._project_path and self._project_data:
            self._write_json(self._project_path, self._project_data)

    @staticmethod
    def _write_json(path: Path, data: dict[str, Any]) -> None:
        """Write a dict to a JSON file, creating parent dirs as needed."""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            log.error("Failed to write settings to %s: %s", path, exc)

    # ------------------------------------------------------------------
    # Getters
    # ------------------------------------------------------------------

    def get(self, key: str, default: Any = None) -> Any:
        """Get a setting value. Project scope overrides user scope."""
        if key in self._project_data:
            return self._project_data[key]
        return self._user_data.get(key, default)

    def list_settings(self) -> dict[str, dict[str, Any]]:
        """Return all settings with their values and source info.

        Returns a dict mapping key -> {"value": ..., "source": "user"|"project"}.
        """
        result: dict[str, dict[str, Any]] = {}

        for key, value in self._user_data.items():
            result[key] = {"value": value, "source": "user"}

        # Project overrides user
        for key, value in self._project_data.items():
            result[key] = {"value": value, "source": "project"}

        return result

    def get_all_sources(self) -> dict[str, Any]:
        """Return merged settings (project overrides user)."""
        merged = dict(self._user_data)
        merged.update(self._project_data)
        return merged

    # ------------------------------------------------------------------
    # Setters
    # ------------------------------------------------------------------

    def set(self, key: str, value: Any, scope: str = "user") -> None:
        """Set a setting value in the specified scope.

        Args:
            key: Setting name.
            value: Setting value (must be JSON-serializable).
            scope: "user" or "project".
        """
        if scope == "project":
            if self._project_path is None:
                raise ValueError(
                    "No project directory configured; "
                    "cannot set project-scope settings"
                )
            self._project_data[key] = value
        else:
            self._user_data[key] = value

    def reset(self, key: str, scope: str = "user") -> None:
        """Remove a setting from the specified scope.

        Args:
            key: Setting name to remove.
            scope: "user" or "project".
        """
        if scope == "project":
            self._project_data.pop(key, None)
        else:
            self._user_data.pop(key, None)
