"""JARVIS Plugin System — discover and run user plugins."""

import importlib.util
import json
import logging
from pathlib import Path
from typing import Optional

from src.config import JARVIS_HOME, ensure_dirs

logger = logging.getLogger(__name__)

# Plugin directories: global (~/.jarvis/plugins/) and local (.jarvis/plugins/)
PLUGIN_DIRS = [
    JARVIS_HOME / "plugins",
    Path(".jarvis") / "plugins",
]


class PluginInfo:
    """Metadata about a loaded plugin."""

    __slots__ = ("name", "description", "triggers", "module", "path")

    def __init__(self, name: str, description: str, triggers: list[str],
                 module, path: Path):
        self.name = name
        self.description = description
        self.triggers = triggers
        self.module = module
        self.path = path

    def __repr__(self) -> str:
        return f"<Plugin {self.name!r} triggers={self.triggers}>"


class PluginManager:
    """Load Python plugins that can intercept queries before hitting the LLM."""

    def __init__(self):
        self._plugins: list[PluginInfo] = []

    # ── Discovery ─────────────────────────────────────────────────────

    def discover(self) -> int:
        """Scan plugin directories and load every valid plugin.

        Returns the number of plugins loaded.
        """
        ensure_dirs()
        self._plugins.clear()
        found = 0

        for plugin_dir in PLUGIN_DIRS:
            plugin_dir = plugin_dir.resolve()
            if not plugin_dir.is_dir():
                continue
            for py_file in sorted(plugin_dir.glob("*.py")):
                if py_file.name.startswith("_"):
                    continue
                try:
                    info = self._load_plugin(py_file)
                    if info is not None:
                        self._plugins.append(info)
                        found += 1
                except Exception as exc:
                    logger.warning("Failed to load plugin %s: %s", py_file, exc)

        # Also load bundled plugins from src/plugins/builtinPlugins
        try:
            from src.plugins.builtinPlugins import get_builtin_plugins
            result = get_builtin_plugins()
            enabled_list = result.get("enabled", []) if isinstance(result, dict) else []
            for loaded in enabled_list:
                name = loaded.name if hasattr(loaded, 'name') else str(loaded)
                if not any(p.name == name for p in self._plugins):
                    info = PluginInfo(
                        name=name,
                        description=getattr(loaded, 'manifest', {}).get('description', ''),
                        triggers=[],
                        module=None,
                        path=Path("builtin"),
                    )
                    self._plugins.append(info)
                    found += 1
        except Exception as exc:
            logger.debug("Bundled plugins not loaded: %s", exc)

        logger.info("Discovered %d plugin(s)", found)
        return found

    def reload(self) -> int:
        """Re-discover plugins (alias kept for callers that expect it)."""
        return self.discover()

    # ── Query handling ────────────────────────────────────────────────

    def handle(self, query: str) -> Optional[str]:
        """Run *query* through every plugin; return first non-None result."""
        for plugin in self._plugins:
            # If triggers are defined, only fire when one matches
            if plugin.triggers:
                q_lower = query.lower()
                if not any(t in q_lower for t in plugin.triggers):
                    continue
            try:
                result = plugin.module.handle(query)
                if result is not None:
                    return result
            except Exception as exc:
                logger.warning("Plugin %s raised: %s", plugin.name, exc)
        return None

    # ── Introspection ─────────────────────────────────────────────────

    def list_plugins(self) -> list[str]:
        """Return the names of all loaded plugins."""
        return [p.name for p in self._plugins]

    def get_plugin(self, name: str) -> Optional[PluginInfo]:
        for p in self._plugins:
            if p.name == name:
                return p
        return None

    def __len__(self) -> int:
        return len(self._plugins)

    def __iter__(self):
        return iter(self._plugins)

    # ── Internal ──────────────────────────────────────────────────────

    def _load_plugin(self, py_file: Path) -> Optional[PluginInfo]:
        """Import a single .py file and wrap it in PluginInfo."""
        spec = importlib.util.spec_from_file_location(
            f"jarvis_plugin.{py_file.stem}", py_file,
        )
        if spec is None or spec.loader is None:
            return None

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        # A plugin MUST expose a `handle(query) -> str | None` callable
        if not callable(getattr(module, "handle", None)):
            logger.debug("Skipping %s — no handle() function", py_file.name)
            return None

        # Read optional plugin.json sitting beside the .py
        meta = self._read_metadata(py_file)
        name = meta.get("name", py_file.stem)
        description = meta.get("description", "")
        triggers = [t.lower() for t in meta.get("triggers", [])]

        return PluginInfo(
            name=name,
            description=description,
            triggers=triggers,
            module=module,
            path=py_file,
        )

    @staticmethod
    def _read_metadata(py_file: Path) -> dict:
        """Read plugin.json next to the .py file, if it exists."""
        json_path = py_file.with_suffix(".json")
        if not json_path.exists():
            # Also check for a single plugin.json in the directory
            dir_json = py_file.parent / "plugin.json"
            if dir_json.exists():
                json_path = dir_json
            else:
                return {}
        try:
            return json.loads(json_path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Bad plugin.json at %s: %s", json_path, exc)
            return {}
