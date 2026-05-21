"""Central registry for JARVIS voice-agent tools.

Ported from ``hermes/tools/registry.py`` — STRIPPED of Hermes-only coupling
(``model_tools``, ACP, gateway, MCP refresh, OpenAI-format ``get_definitions``).
What's kept and load-bearing:

  * ``ToolEntry`` — schema + handler + availability metadata for one tool.
  * module-level ``registry`` singleton with ``register(...)``.
  * ``discover_builtin_tools()`` — AST-scan this dir for modules that make a
    top-level ``registry.register(...)`` call, then import them so their
    registration side-effects run.
  * the ``check_fn`` TTL cache (external-state probes are expensive; results
    are cached ~30 s so env-var flips still propagate within a turn or two).
  * ``all_entries()`` — accessor returning every registered ``ToolEntry``,
    consumed by ``_hermes_adapter.load_all_livekit_tools()``.

The LiveKit voice agent does NOT use this registry's OpenAI-format schema
emission; ``_hermes_adapter.to_livekit_tool`` converts each ``ToolEntry`` into
a LiveKit ``RawFunctionTool`` instead. Keep this module stdlib-only and free
of any ``import jarvis_agent`` / livekit dependency so the import chain stays
circular-import safe:

    tools/registry.py        (no imports from tool files or the adapter)
           ^
    tools/*.py               (call registry.register at module level)
           ^
    tools/_hermes_adapter.py (imports registry + adapts every entry)
"""
from __future__ import annotations

import ast
import importlib
import logging
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# AST discovery
# ---------------------------------------------------------------------------


def _is_registry_register_call(node: ast.AST) -> bool:
    """Return True when *node* is a ``registry.register(...)`` call expression."""
    if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
        return False
    func = node.value.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "register"
        and isinstance(func.value, ast.Name)
        and func.value.id == "registry"
    )


def _module_registers_tools(module_path: Path) -> bool:
    """Return True when the module has a top-level ``registry.register(...)`` call.

    Only inspects module-body statements so that helper modules which happen to
    call ``registry.register()`` inside a function are not picked up.
    """
    try:
        source = module_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(module_path))
    except (OSError, SyntaxError):
        return False
    return any(_is_registry_register_call(stmt) for stmt in tree.body)


# Modules in this dir that are framework infrastructure, not tool files.
_NON_TOOL_MODULES = {
    "__init__.py",
    "registry.py",
    "_hermes_adapter.py",
    "runtime.py",
}


def discover_builtin_tools(tools_dir: Optional[Path] = None) -> List[str]:
    """Import self-registering tool modules; return their imported module names.

    AST-scans *tools_dir* (defaults to this package dir) for ``*.py`` files that
    make a module-level ``registry.register(...)`` call, then imports each so its
    registration side effect runs. Import failures are logged and skipped so one
    broken tool can't take down the whole surface.
    """
    tools_path = Path(tools_dir) if tools_dir is not None else Path(__file__).resolve().parent
    pkg = __package__ or tools_path.name
    module_names = [
        f"{pkg}.{path.stem}"
        for path in sorted(tools_path.glob("*.py"))
        if path.name not in _NON_TOOL_MODULES and _module_registers_tools(path)
    ]

    imported: List[str] = []
    for mod_name in module_names:
        try:
            importlib.import_module(mod_name)
            imported.append(mod_name)
        except Exception as e:  # pragma: no cover - exercised via broken tools only
            logger.warning("Could not import tool module %s: %s", mod_name, e)
    return imported


# ---------------------------------------------------------------------------
# ToolEntry
# ---------------------------------------------------------------------------


class ToolEntry:
    """Metadata for a single registered tool."""

    __slots__ = (
        "name",
        "toolset",
        "schema",
        "handler",
        "check_fn",
        "requires_env",
        "is_async",
        "description",
        "emoji",
        "max_result_size_chars",
    )

    def __init__(
        self,
        name: str,
        toolset: str,
        schema: dict,
        handler: Callable,
        check_fn: Optional[Callable] = None,
        requires_env: Optional[list] = None,
        is_async: bool = False,
        description: str = "",
        emoji: str = "",
        max_result_size_chars: int | float | None = None,
    ):
        self.name = name
        self.toolset = toolset
        self.schema = schema
        self.handler = handler
        self.check_fn = check_fn
        self.requires_env = requires_env or []
        self.is_async = is_async
        self.description = description or schema.get("description", "")
        self.emoji = emoji
        self.max_result_size_chars = max_result_size_chars

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<ToolEntry {self.name!r} toolset={self.toolset!r} async={self.is_async}>"


# ---------------------------------------------------------------------------
# check_fn TTL cache
#
# check_fn callables probe external state (binary availability, daemon
# reachability, credential files). Calling them on every tool-list build is
# pure waste — external state changes on human timescales. Cache results for
# ~30 s so env-var flips / credential-file changes propagate within a turn or
# two without any explicit invalidation.
# ---------------------------------------------------------------------------

_CHECK_FN_TTL_SECONDS = 30.0
_check_fn_cache: Dict[Callable, tuple[float, bool]] = {}
_check_fn_cache_lock = threading.Lock()


def _check_fn_cached(fn: Callable) -> bool:
    """Return ``bool(fn())``, TTL-cached across calls. Swallows exceptions as False."""
    now = time.monotonic()
    with _check_fn_cache_lock:
        cached = _check_fn_cache.get(fn)
        if cached is not None:
            ts, value = cached
            if now - ts < _CHECK_FN_TTL_SECONDS:
                return value
    try:
        value = bool(fn())
    except Exception:
        value = False
    with _check_fn_cache_lock:
        _check_fn_cache[fn] = (now, value)
    return value


def invalidate_check_fn_cache() -> None:
    """Drop all cached ``check_fn`` results. Call after config changes that
    affect tool availability."""
    with _check_fn_cache_lock:
        _check_fn_cache.clear()


# ---------------------------------------------------------------------------
# ToolRegistry
# ---------------------------------------------------------------------------


class ToolRegistry:
    """Singleton registry that collects tool schemas + handlers from tool files."""

    def __init__(self) -> None:
        self._tools: Dict[str, ToolEntry] = {}
        # Mutations can race readers (e.g. discovery on one thread, adapt on
        # another); keep mutations serialized and readers on stable snapshots.
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(
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
        """Register a tool. Called at module-import time by each tool file.

        ``toolset`` defaults to ``"builtin"`` (the Hermes positional ``toolset``
        arg is keyword-only here since the voice-agent doesn't use toolset
        gating yet — a single flat surface). ``override=True`` is an explicit
        opt-in to replace an existing tool of the same name from a *different*
        toolset; without it, a shadowing registration is rejected and logged.
        """
        toolset = toolset or "builtin"
        with self._lock:
            existing = self._tools.get(name)
            if existing is not None and existing.toolset != toolset and not override:
                logger.error(
                    "Tool registration REJECTED: '%s' (toolset '%s') would shadow "
                    "existing tool from toolset '%s'. Pass override=True if intentional.",
                    name,
                    toolset,
                    existing.toolset,
                )
                return
            if existing is not None and override:
                logger.info(
                    "Tool '%s': toolset '%s' overriding existing toolset '%s' (override=True)",
                    name,
                    toolset,
                    existing.toolset,
                )
            self._tools[name] = ToolEntry(
                name=name,
                toolset=toolset,
                schema=schema,
                handler=handler,
                check_fn=check_fn,
                requires_env=requires_env or [],
                is_async=is_async,
                description=description if description is not None else schema.get("description", ""),
                emoji=emoji or "",
                max_result_size_chars=max_result_size_chars,
            )

    def deregister(self, name: str) -> None:
        """Remove a tool from the registry (no-op if absent)."""
        with self._lock:
            self._tools.pop(name, None)

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def all_entries(self) -> List[ToolEntry]:
        """Return a stable snapshot of every registered ToolEntry."""
        with self._lock:
            return list(self._tools.values())

    def get_entry(self, name: str) -> Optional[ToolEntry]:
        """Return a registered tool entry by name, or None."""
        with self._lock:
            return self._tools.get(name)

    def get_schema(self, name: str) -> Optional[dict]:
        """Return a tool's raw schema dict, bypassing check_fn filtering."""
        entry = self.get_entry(name)
        return entry.schema if entry else None

    def all_names(self) -> List[str]:
        """Return sorted list of all registered tool names."""
        with self._lock:
            return sorted(self._tools)

    def is_available(self, name: str) -> bool:
        """Return whether a tool's ``check_fn`` currently passes (True if none)."""
        entry = self.get_entry(name)
        if entry is None:
            return False
        if entry.check_fn is None:
            return True
        return _check_fn_cached(entry.check_fn)


# Module-level singleton — tool files call ``registry.register(...)`` against it.
registry = ToolRegistry()


# ---------------------------------------------------------------------------
# Module-level convenience accessors (mirror Hermes' top-level helpers)
# ---------------------------------------------------------------------------


def all_entries() -> List[ToolEntry]:
    """Return a snapshot of every registered ToolEntry (module-level shortcut)."""
    return registry.all_entries()


# ---------------------------------------------------------------------------
# Tool-response serialization helpers (ported verbatim — handlers may use them)
# ---------------------------------------------------------------------------


def tool_error(message, **extra) -> str:
    """Return a JSON error string for tool handlers.

    >>> tool_error("file not found")
    '{"error": "file not found"}'
    """
    import json

    result = {"error": str(message)}
    if extra:
        result.update(extra)
    return json.dumps(result, ensure_ascii=False)


def tool_result(data=None, **kwargs) -> str:
    """Return a JSON result string for tool handlers.

    Accepts a dict positional arg *or* keyword arguments (not both):

    >>> tool_result(success=True, count=42)
    '{"success": true, "count": 42}'
    """
    import json

    if data is not None:
        return json.dumps(data, ensure_ascii=False)
    return json.dumps(kwargs, ensure_ascii=False)
