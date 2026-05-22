"""Minimal plugin system for the JARVIS voice agent.

Lets plugins contribute tools onto the supervisor by delegating to the
existing :mod:`tools.registry`. A plugin is a directory holding a
``plugin.yaml`` manifest and an ``__init__.py`` with a ``register(ctx)``
function. On discovery each plugin's ``__init__.py`` is imported under the
``jarvis_plugins.<slug>`` namespace and its ``register(ctx)`` is called with a
:class:`PluginContext`. The context's :meth:`PluginContext.register_tool`
forwards straight into ``tools.registry.register(...)`` so plugin tools appear
alongside the built-ins and flow through ``_adapter.load_all_livekit_tools()``
unchanged.

Discovery sources (later overrides earlier on name collision):

  1. **Bundled** — ``src/voice-agent/plugins/<name>/`` (ships with the agent).
  2. **User**    — ``~/.jarvis/plugins/<name>/`` (user-installed; wins).

Both flat (``<root>/<name>/plugin.yaml``) and 2-level category
(``<root>/<category>/<name>/plugin.yaml``) layouts are supported.

This is deliberately minimal: no config.yaml, no entry-points, no project
plugins, no gateway/ACP/CLI coupling. Bundled plugins load by default; an
optional ``JARVIS_PLUGINS_DISABLED`` env (comma-separated names) acts as a
denylist. The non-tool contribution methods (hooks, skills, context engines,
providers, commands, ...) are stubbed as logging no-ops — the voice agent
doesn't consume those yet, but plugins that call them must not crash.

Kept stdlib-only + free of ``import jarvis_agent`` / livekit so the import
chain stays circular-import safe, mirroring ``tools/registry.py``::

    tools/registry.py        (no imports from tool files or the adapter)
           ^
    tools/plugin_system.py   (delegates register_tool -> registry.register)
           ^
    tools/_adapter.py        (calls discover_plugins(), then adapts entries)
"""
from __future__ import annotations

import importlib.util
import logging
import os
import sys
import threading
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Union

try:  # PyYAML is present in the voice-agent .venv (6.0.3); fall back if absent.
    import yaml
except ImportError:  # pragma: no cover - yaml is available in the target venv
    yaml = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# Namespace parent package each plugin's __init__.py is imported under, e.g.
# a bundled plugin "example" imports as ``jarvis_plugins.example``. JARVIS-native
# module namespace — keep it under this prefix so plugin modules never collide
# with the agent's own packages.
_NS_PARENT = "jarvis_plugins"

_TRUE_TOKENS = {"1", "true", "yes", "on"}


def _env_truthy(name: str) -> bool:
    """Return True when env var *name* is set to a truthy opt-in value."""
    return os.environ.get(name, "").strip().lower() in _TRUE_TOKENS


def _get_disabled_plugins() -> Set[str]:
    """Read the optional ``JARVIS_PLUGINS_DISABLED`` denylist (comma-separated).

    A plugin whose name OR path-derived key appears here never loads, even
    though all bundled plugins are otherwise enabled by default.
    """
    raw = os.environ.get("JARVIS_PLUGINS_DISABLED", "")
    return {part.strip() for part in raw.split(",") if part.strip()}


def get_bundled_plugins_dir() -> Path:
    """Locate the bundled ``plugins/`` directory (``src/voice-agent/plugins/``).

    Honours ``JARVIS_BUNDLED_PLUGINS`` for packaged installs; otherwise the
    in-repo path one level up from this ``tools/`` package.
    """
    override = os.environ.get("JARVIS_BUNDLED_PLUGINS")
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent / "plugins"


def get_user_plugins_dir() -> Path:
    """Locate the user plugins directory (``~/.jarvis/plugins/``)."""
    override = os.environ.get("JARVIS_USER_PLUGINS")
    if override:
        return Path(override)
    return Path.home() / ".jarvis" / "plugins"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class PluginManifest:
    """Parsed representation of a ``plugin.yaml`` manifest (flat schema)."""

    name: str
    version: str = ""
    description: str = ""
    kind: str = "standalone"
    requires_env: List[Union[str, Dict[str, Any]]] = field(default_factory=list)
    provides_tools: List[str] = field(default_factory=list)
    source: str = ""  # "bundled" or "user"
    path: Optional[str] = None
    # Path-derived registry key. Flat plugin ``plugins/example/`` → ``example``;
    # category plugin ``plugins/browser/cdp/`` → ``browser/cdp``. Falls back to
    # ``name`` when empty.
    key: str = ""


@dataclass
class LoadedPlugin:
    """Runtime state for a single discovered plugin."""

    manifest: PluginManifest
    module: Optional[types.ModuleType] = None
    tools_registered: List[str] = field(default_factory=list)
    enabled: bool = False
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Manifest parsing
# ---------------------------------------------------------------------------


def _hand_parse_manifest(text: str) -> Dict[str, Any]:
    """Parse a simple flat ``plugin.yaml`` without PyYAML.

    Supports exactly what :class:`PluginManifest` needs: scalar ``key: value``
    pairs (name/version/description/kind) and flat list values for
    ``provides_tools`` / ``requires_env`` in either inline ``[a, b]`` form or
    block form::

        provides_tools:
          - tool_a
          - tool_b

    Lines starting with ``#`` and blank lines are ignored. Quotes around scalar
    values are stripped. This is a deliberately tiny parser — manifests are
    author-controlled and flat, so we don't need full YAML.
    """
    data: Dict[str, Any] = {}
    current_list_key: Optional[str] = None
    for raw_line in text.splitlines():
        # Strip trailing comments only when not inside a quoted value (manifests
        # are simple; we don't support '#' inside values).
        line = raw_line.split("#", 1)[0].rstrip()
        if not line:
            continue

        stripped = line.lstrip()
        indent = len(line) - len(stripped)

        # Block-list item: "- value" under a previously-seen list key.
        if stripped.startswith("- ") or stripped == "-":
            if current_list_key is not None and indent > 0:
                item = stripped[1:].strip().strip("'\"")
                if item:
                    data.setdefault(current_list_key, []).append(item)
                continue
            # A dash at column 0 with no active key — ignore.
            continue

        if ":" not in stripped:
            continue

        key, _, value = stripped.partition(":")
        key = key.strip()
        value = value.strip()
        current_list_key = None

        if value == "":
            # Either an empty scalar or the header of a block list. Mark it as
            # the active list key; if the next lines aren't "- " items it stays
            # an empty string.
            current_list_key = key
            data.setdefault(key, [])
            continue

        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            items = [p.strip().strip("'\"") for p in inner.split(",") if p.strip()]
            data[key] = items
            continue

        data[key] = value.strip("'\"")

    # Collapse list keys that turned out to have no items AND no scalar back to
    # an empty string for scalar fields (name/version/description/kind), but
    # leave genuine list fields as lists.
    for scalar_key in ("name", "version", "description", "kind"):
        if isinstance(data.get(scalar_key), list) and not data[scalar_key]:
            data[scalar_key] = ""
    return data


def _load_manifest_data(manifest_file: Path) -> Dict[str, Any]:
    """Load the raw manifest dict, using PyYAML when available else hand-parse."""
    text = manifest_file.read_text(encoding="utf-8")
    if yaml is not None:
        loaded = yaml.safe_load(text)
        return loaded or {}
    return _hand_parse_manifest(text)


def _coerce_list(value: Any) -> List[Any]:
    """Coerce a manifest field to a list (tolerate scalar / None)."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


# ---------------------------------------------------------------------------
# PluginContext — handed to each plugin's register() function
# ---------------------------------------------------------------------------


class PluginContext:
    """Facade given to plugins so they can contribute tools to the agent.

    The only fully-wired contribution is :meth:`register_tool`, which delegates
    to the global ``tools.registry``. The remaining ``register_*`` methods are
    forward-compatibility stubs: the voice agent doesn't consume hooks, skills,
    context engines, or providers yet, but a plugin authored against the
    upstream surface must not crash when it calls them.
    """

    def __init__(self, manifest: PluginManifest, manager: "PluginManager") -> None:
        self.manifest = manifest
        self._manager = manager

    # -- tool registration (the real wiring) --------------------------------

    def register_tool(
        self,
        name: str,
        schema: dict,
        handler: Callable,
        *,
        toolset: Optional[str] = None,
        check_fn: Optional[Callable] = None,
        requires_env: Optional[list] = None,
        is_async: bool = False,
        description: Optional[str] = None,
        emoji: Optional[str] = None,
        max_result_size_chars: int | float | None = None,
        override: bool = False,
    ) -> None:
        """Register a tool in the global registry and track it as plugin-provided.

        Forwards every argument straight to ``tools.registry.register(...)`` —
        same shape, same semantics. Pass ``override=True`` to replace a built-in
        tool of the same name from a different toolset (e.g. swap a default
        ``browser_navigate`` for a plugin-backed implementation); without it a
        shadowing registration is rejected by the registry.

        ``toolset`` defaults (in the registry) to ``"builtin"``; plugins are
        encouraged to pass their own toolset name so their tools group cleanly.
        """
        from tools.registry import registry

        registry.register(
            name=name,
            schema=schema,
            handler=handler,
            toolset=toolset,
            check_fn=check_fn,
            requires_env=requires_env,
            is_async=is_async,
            description=description,
            emoji=emoji,
            max_result_size_chars=max_result_size_chars,
            override=override,
        )
        self._manager._plugin_tool_names.add(name)
        logger.debug(
            "Plugin %s registered tool: %s%s",
            self.manifest.name,
            name,
            " (override)" if override else "",
        )

    # -- forward-compat stubs (no-op in the voice agent) --------------------
    #
    # Each logs at debug and returns None so a plugin written against the fuller
    # upstream contribution surface degrades gracefully here instead of raising
    # AttributeError. Wire any of these up for real when the voice agent grows a
    # consumer for it.

    def _stub(self, what: str, detail: str = "") -> None:
        logger.debug(
            "Plugin %s called register_%s — not consumed by the voice agent "
            "(no-op)%s",
            self.manifest.name,
            what,
            f": {detail}" if detail else "",
        )

    def register_hook(self, hook_name: str, callback: Callable) -> None:
        """No-op stub: lifecycle hooks aren't consumed by the voice agent yet."""
        self._stub("hook", hook_name)

    def register_skill(self, name: str, path: Path, description: str = "") -> None:
        """No-op stub: plugin skills aren't consumed by the voice agent yet."""
        self._stub("skill", name)

    def register_context_engine(self, engine: Any) -> None:
        """No-op stub: context-engine override isn't consumed by the voice agent."""
        self._stub("context_engine")

    def register_memory_provider(self, provider: Any) -> None:
        """No-op stub: memory providers aren't consumed by the voice agent."""
        self._stub("memory_provider")

    def register_cli_command(
        self,
        name: str,
        help: str = "",
        setup_fn: Optional[Callable] = None,
        handler_fn: Optional[Callable] = None,
        description: str = "",
    ) -> None:
        """No-op stub: there is no CLI surface in the voice agent."""
        self._stub("cli_command", name)

    def register_command(
        self,
        name: str,
        handler: Callable,
        description: str = "",
        args_hint: str = "",
    ) -> None:
        """No-op stub: in-session slash commands aren't consumed by the voice agent."""
        self._stub("command", name)

    def register_platform(self, name: str, *args: Any, **kwargs: Any) -> None:
        """No-op stub: gateway platform adapters aren't consumed by the voice agent."""
        self._stub("platform", name)

    # -- provider registration (wired into tools._provider_registry) --------
    #
    # Hermes-shaped backend plugins register a provider object (duck-typed
    # name + is_available() + capability methods) under a capability *kind*.
    # The consuming registry tool (image_generate / video_generate /
    # web_extract / browser_*) resolves it via _provider_registry.get_provider.

    def _register_provider(self, kind: str, provider: Any) -> None:
        """Land a plugin-provided backend in the generic provider registry.

        Name is taken from ``provider.name``. A provider with no usable name is
        skipped with a warning rather than raising — one malformed backend must
        not break discovery of the rest.
        """
        name = str(getattr(provider, "name", "") or "").strip()
        if not name:
            logger.warning(
                "Plugin %s registered a %s provider with no usable .name — skipped",
                self.manifest.name,
                kind,
            )
            return
        from tools import _provider_registry

        _provider_registry.register_provider(kind, name, provider)
        logger.debug(
            "Plugin %s registered %s provider %r", self.manifest.name, kind, name
        )

    def register_image_gen_provider(self, provider: Any) -> None:
        """Register an image-generation backend under the ``image`` kind."""
        self._register_provider("image", provider)

    def register_web_search_provider(self, provider: Any) -> None:
        """Register a web search/extract/crawl backend under the ``web`` kind."""
        self._register_provider("web", provider)

    def register_video_gen_provider(self, provider: Any) -> None:
        """Register a video-generation backend under the ``video`` kind."""
        self._register_provider("video", provider)

    def register_browser_provider(self, provider: Any) -> None:
        """Register a cloud-browser backend under the ``browser`` kind."""
        self._register_provider("browser", provider)


# ---------------------------------------------------------------------------
# PluginManager
# ---------------------------------------------------------------------------


class PluginManager:
    """Discovers, loads, and tracks plugins. Idempotent after the first scan."""

    def __init__(self) -> None:
        self._plugins: Dict[str, LoadedPlugin] = {}
        self._plugin_tool_names: Set[str] = set()
        self._discovered: bool = False
        self._lock = threading.RLock()

    # -- public --------------------------------------------------------------

    def discover_and_load(self, force: bool = False) -> None:
        """Scan bundled + user plugin dirs and load each plugin found.

        Idempotent: the first call does the work and caches; later calls are
        no-ops unless ``force=True`` (rescan + reload in the current process).
        """
        with self._lock:
            if self._discovered and not force:
                return
            if force:
                self._plugins.clear()
                self._plugin_tool_names.clear()
            self._discovered = True

            manifests: List[PluginManifest] = []

            bundled_dir = get_bundled_plugins_dir()
            logger.debug("Scanning bundled plugins: %s", bundled_dir)
            manifests.extend(self._scan_directory(bundled_dir, source="bundled"))

            user_dir = get_user_plugins_dir()
            logger.debug("Scanning user plugins: %s", user_dir)
            manifests.extend(self._scan_directory(user_dir, source="user"))

            # Later sources override earlier ones on key collision: a user
            # plugin replaces a bundled one of the same key. Dedup so only the
            # final winner loads.
            winners: Dict[str, PluginManifest] = {}
            for manifest in manifests:
                winners[manifest.key or manifest.name] = manifest

            disabled = _get_disabled_plugins()
            for manifest in winners.values():
                lookup_key = manifest.key or manifest.name
                if lookup_key in disabled or manifest.name in disabled:
                    loaded = LoadedPlugin(manifest=manifest, enabled=False)
                    loaded.error = "disabled via JARVIS_PLUGINS_DISABLED"
                    self._plugins[lookup_key] = loaded
                    logger.debug("Skipping disabled plugin '%s'", lookup_key)
                    continue
                self._load_plugin(manifest)

            if manifests:
                logger.info(
                    "Plugin discovery complete: %d found, %d enabled",
                    len(self._plugins),
                    sum(1 for p in self._plugins.values() if p.enabled),
                )

    # -- directory scanning --------------------------------------------------

    def _scan_directory(self, path: Path, source: str) -> List[PluginManifest]:
        """Read ``plugin.yaml`` manifests under *path* (flat + 2-level category).

        * Flat — ``<root>/<name>/plugin.yaml`` → key ``<name>``.
        * Category — ``<root>/<category>/<name>/plugin.yaml`` (the ``<category>``
          dir itself has no manifest) → key ``<category>/<name>``. Capped at two
          segments deep.
        """
        return self._scan_level(path, source, prefix="", depth=0)

    def _scan_level(
        self, path: Path, source: str, *, prefix: str, depth: int
    ) -> List[PluginManifest]:
        manifests: List[PluginManifest] = []
        if not path.is_dir():
            return manifests

        for child in sorted(path.iterdir()):
            if not child.is_dir():
                continue
            manifest_file = child / "plugin.yaml"
            if not manifest_file.exists():
                manifest_file = child / "plugin.yml"

            if manifest_file.exists():
                manifest = self._parse_manifest(manifest_file, child, source, prefix)
                if manifest is not None:
                    manifests.append(manifest)
                continue

            # No manifest here. Within the depth cap, treat this as a category
            # namespace and recurse one level looking for child manifests.
            if depth >= 1:
                logger.debug("Skipping %s (no plugin.yaml, depth cap reached)", child)
                continue
            sub_prefix = f"{prefix}/{child.name}" if prefix else child.name
            manifests.extend(
                self._scan_level(child, source, prefix=sub_prefix, depth=depth + 1)
            )

        return manifests

    def _parse_manifest(
        self, manifest_file: Path, plugin_dir: Path, source: str, prefix: str
    ) -> Optional[PluginManifest]:
        """Parse one ``plugin.yaml`` into a :class:`PluginManifest` (None on error)."""
        try:
            data = _load_manifest_data(manifest_file)
            if not isinstance(data, dict):
                logger.warning("Manifest %s is not a mapping; skipping", manifest_file)
                return None

            name = str(data.get("name") or plugin_dir.name)
            key = f"{prefix}/{plugin_dir.name}" if prefix else name

            kind = str(data.get("kind") or "standalone").strip().lower() or "standalone"

            return PluginManifest(
                name=name,
                version=str(data.get("version", "")),
                description=str(data.get("description", "")),
                kind=kind,
                requires_env=_coerce_list(data.get("requires_env")),
                provides_tools=_coerce_list(data.get("provides_tools")),
                source=source,
                path=str(plugin_dir),
                key=key,
            )
        except Exception as exc:  # noqa: BLE001 — a bad manifest must not break discovery
            logger.warning("Failed to parse %s: %s", manifest_file, exc)
            return None

    # -- loading -------------------------------------------------------------

    def _load_plugin(self, manifest: PluginManifest) -> None:
        """Import a plugin's ``__init__.py`` and call its ``register(ctx)``."""
        loaded = LoadedPlugin(manifest=manifest)
        lookup_key = manifest.key or manifest.name
        logger.debug(
            "Loading plugin '%s' (source=%s, path=%s)",
            lookup_key,
            manifest.source,
            manifest.path,
        )

        # Snapshot tool names already present so we can attribute newly-added
        # ones to this plugin.
        before = set(self._plugin_tool_names)
        try:
            module = self._import_plugin_module(manifest)
            loaded.module = module

            register_fn = getattr(module, "register", None)
            if register_fn is None:
                loaded.error = "no register() function"
                logger.warning("Plugin '%s' has no register() function", lookup_key)
            else:
                ctx = PluginContext(manifest, self)
                register_fn(ctx)
                loaded.tools_registered = sorted(self._plugin_tool_names - before)
                loaded.enabled = True
                logger.debug(
                    "  plugin '%s' registered %d tool(s): %s",
                    lookup_key,
                    len(loaded.tools_registered),
                    ", ".join(loaded.tools_registered) or "(none)",
                )
        except Exception as exc:  # noqa: BLE001 — one broken plugin must not break the rest
            loaded.error = str(exc)
            logger.warning("Failed to load plugin '%s': %s", lookup_key, exc)

        self._plugins[lookup_key] = loaded

    def _import_plugin_module(self, manifest: PluginManifest) -> types.ModuleType:
        """Import a directory plugin's ``__init__.py`` as ``jarvis_plugins.<slug>``.

        The slug is derived from the path-key so a category plugin
        ``browser/cdp`` imports as ``jarvis_plugins.browser__cdp`` without
        colliding with any sibling.
        """
        plugin_dir = Path(manifest.path)  # type: ignore[arg-type]
        init_file = plugin_dir / "__init__.py"
        if not init_file.exists():
            raise FileNotFoundError(f"No __init__.py in {plugin_dir}")

        # Ensure the namespace parent package exists so submodule imports resolve.
        if _NS_PARENT not in sys.modules:
            ns_pkg = types.ModuleType(_NS_PARENT)
            ns_pkg.__path__ = []  # type: ignore[attr-defined]
            ns_pkg.__package__ = _NS_PARENT
            sys.modules[_NS_PARENT] = ns_pkg

        key = manifest.key or manifest.name
        slug = key.replace("/", "__").replace("-", "_")
        module_name = f"{_NS_PARENT}.{slug}"
        spec = importlib.util.spec_from_file_location(
            module_name,
            init_file,
            submodule_search_locations=[str(plugin_dir)],
        )
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot create module spec for {init_file}")

        module = importlib.util.module_from_spec(spec)
        module.__package__ = module_name
        module.__path__ = [str(plugin_dir)]  # type: ignore[attr-defined]
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module

    # -- introspection -------------------------------------------------------

    def list_plugins(self) -> List[Dict[str, Any]]:
        """Return info dicts for all discovered plugins (sorted by key)."""
        result: List[Dict[str, Any]] = []
        for key, loaded in sorted(self._plugins.items()):
            result.append(
                {
                    "name": loaded.manifest.name,
                    "key": loaded.manifest.key or loaded.manifest.name,
                    "kind": loaded.manifest.kind,
                    "version": loaded.manifest.version,
                    "description": loaded.manifest.description,
                    "source": loaded.manifest.source,
                    "enabled": loaded.enabled,
                    "tools": list(loaded.tools_registered),
                    "error": loaded.error,
                }
            )
        return result


# ---------------------------------------------------------------------------
# Module-level singleton + convenience entry point
# ---------------------------------------------------------------------------

_plugin_manager: Optional[PluginManager] = None
_manager_lock = threading.Lock()


def get_plugin_manager() -> PluginManager:
    """Return (and lazily create) the global :class:`PluginManager` singleton."""
    global _plugin_manager
    if _plugin_manager is None:
        with _manager_lock:
            if _plugin_manager is None:
                _plugin_manager = PluginManager()
    return _plugin_manager


def discover_plugins(force: bool = False) -> PluginManager:
    """Discover and load all plugins; return the manager.

    Idempotent by default — safe to call from ``load_all_livekit_tools()`` on
    every tool-surface build. Pass ``force=True`` to rescan in-process.
    """
    manager = get_plugin_manager()
    manager.discover_and_load(force=force)
    return manager
