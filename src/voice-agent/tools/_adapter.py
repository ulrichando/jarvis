"""Adapter: ``ToolEntry`` → LiveKit 1.5.x ``RawFunctionTool``.

The keystone of the tool layer. JARVIS declares a tool as a JSON schema + a
handler. LiveKit's ``function_tool(handler, raw_schema=<dict>)`` turns a raw
schema + handler into a ``RawFunctionTool`` the voice supervisor can register
and call. This module bridges the two:

  * ``to_livekit_tool(entry)`` — wrap one ``ToolEntry`` into a ``RawFunctionTool``.
  * ``sanitize_schema(params)`` — force ``additionalProperties: false`` on every
    object node in a parameters schema (Anthropic supervisor HARD requirement —
    see ``sanitizers/anthropic_strict_schema.py``; we apply it at build time so
    the schema is correct before it ever reaches the LLM).
  * ``load_all_livekit_tools()`` — discover every self-registered tool and adapt
    them all, skipping (with a warning) any whose ``check_fn`` is False or that
    fail to adapt. A single broken tool must not take down the whole surface.

CALLING CONVENTION (verified against livekit-agents 1.5.9
``llm/utils.py::pydantic_arguments_from_function`` / ``llm/tool_context.py``):
the framework invokes a raw tool's handler with the JSON arguments bound to a
parameter literally named ``raw_arguments``. Our wrapped handler is therefore
``async def _run(raw_arguments: dict)`` — the name is load-bearing; do not
rename it. The framework also injects a ``RunContext``-typed param if present;
we don't declare one (tool handlers don't take it).
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import os
from typing import Any, List, Optional

from livekit.agents.llm import RawFunctionTool, function_tool

from .registry import ToolEntry, discover_builtin_tools, registry

logger = logging.getLogger(__name__)

__all__ = [
    "to_livekit_tool",
    "load_all_livekit_tools",
    "sanitize_schema",
    "LOCAL_VOICE_CORE_TOOLS",
    "local_voice_tool_allowlist",
]


# ---------------------------------------------------------------------------
# Local-mode core tool surface (2026-07-11)
#
# In local voice mode (~/.jarvis/voice-mode == "local") the supervisor runs a
# small on-device model (qwen3-4b class) whose context window is 12288 tokens
# — the FULL registry surface (~50 tools, ~24k tokens of schema alone, with
# computer_use's SOM schema the largest single item) cannot fit. The
# supervisor tool list is therefore trimmed to this deliberately minimal,
# small-schema core (~3.4k tokens): shell + file read/write + memory +
# web_search + clarify. Everything else (computer_use / browser_task /
# dispatch_agent / webcam / face_recognition / skills / agent authoring /
# the web_* project tools / …) is dropped from the LOCAL surface only.
#
# Tune by editing this tuple, or per-box via the JARVIS_LOCAL_VOICE_TOOLS
# env (comma-separated tool names, replaces the default set entirely).
# CLOUD MODE IS UNTOUCHED: the filter is inert unless the local-mode
# predicate (providers.llm.is_local_voice_mode) is True.
# ---------------------------------------------------------------------------
LOCAL_VOICE_CORE_TOOLS: tuple = (
    "terminal",
    "read_file",
    "write_file",
    "memory",
    "web_search",
    "clarify",
    # Small 2-field schema; lets the local supervisor list + run the user's
    # Home scheduled tasks ("run my morning brief"). Only appears when the
    # scheduled-voice token is configured (its own check_fn).
    "scheduled",
)


def local_voice_tool_allowlist() -> Optional[frozenset]:
    """The local-mode tool allowlist, or None when local mode is off (no
    restriction — the full registry surface loads exactly as before).
    Fails toward None (full surface) on any predicate error."""
    try:
        from providers.llm import is_local_voice_mode  # lazy — avoids import cycle
        if not is_local_voice_mode():
            return None
    except Exception:  # noqa: BLE001 — predicate failure must not trim cloud
        return None
    raw = (os.environ.get("JARVIS_LOCAL_VOICE_TOOLS", "") or "").strip()
    if raw:
        names = frozenset(p.strip() for p in raw.split(",") if p.strip())
        if names:
            return names
    return frozenset(LOCAL_VOICE_CORE_TOOLS)


# ---------------------------------------------------------------------------
# Schema sanitizer
#
# Mirrors sanitizers/anthropic_strict_schema.py::fix_schema. Anthropic's
# /v1/messages rejects tool definitions whose object-typed nodes don't set
# `additionalProperties: false`. We apply it at adapt time (rather than relying
# solely on the import-time monkey-patch) so the RawFunctionTool is correct at
# the source — belt-and-suspenders with the patch, and correct even for
# non-Anthropic providers (they tolerate the extra key).
# ---------------------------------------------------------------------------


def sanitize_schema(node: Any) -> Any:
    """Recursively force every ``type: object`` sub-tree to declare
    ``additionalProperties: false``. Mutates in place and returns the same
    reference so callers can chain.

    Handles top-level objects, nested object properties, array ``items``,
    ``anyOf`` / ``oneOf`` / ``allOf`` branches, ``$defs`` / ``definitions``,
    and ``type: ["object", "null"]`` (Optional[dict]) shapes.
    """
    if isinstance(node, list):
        for item in node:
            sanitize_schema(item)
        return node

    if not isinstance(node, dict):
        return node

    t = node.get("type")
    is_object = (
        t == "object"
        or (isinstance(t, list) and "object" in t)
        # `properties` present but `type` implicit → treat as object too.
        or ("properties" in node and t is None)
    )
    if is_object and node.get("additionalProperties") is not False:
        node["additionalProperties"] = False

    for key in ("properties", "patternProperties", "$defs", "definitions"):
        sub = node.get(key)
        if isinstance(sub, dict):
            for v in sub.values():
                sanitize_schema(v)

    for key in ("items", "contains", "not", "if", "then", "else", "additionalItems"):
        sub = node.get(key)
        if sub is not None:
            sanitize_schema(sub)

    for key in ("anyOf", "oneOf", "allOf", "prefixItems"):
        sub = node.get(key)
        if isinstance(sub, list):
            for v in sub:
                sanitize_schema(v)

    return node


# ---------------------------------------------------------------------------
# Single-entry adaptation
# ---------------------------------------------------------------------------


def _extract_parameters(entry: ToolEntry) -> dict:
    """Pull the JSON-schema parameters object out of a ToolEntry's schema.

    JARVIS tool schemas store parameters under ``schema["parameters"]``. Some
    tools may instead carry the parameters at the schema root (an object schema
    with ``properties`` directly). Be permissive: prefer ``parameters``, else
    fall back to an object schema if the root looks like one, else an empty
    object schema (a no-arg tool).
    """
    schema = entry.schema or {}
    params = schema.get("parameters")
    if isinstance(params, dict):
        return params
    if "properties" in schema or schema.get("type") == "object":
        # Root IS the parameters object (strip our descriptive-only keys).
        return {k: v for k, v in schema.items() if k not in ("name", "description")}
    return {"type": "object", "properties": {}}


def _build_wrapped_handler(entry: ToolEntry):
    """Return an async ``_run(raw_arguments)`` that invokes the entry's handler.

    * awaits the handler if ``entry.is_async`` (also awaits if the handler
      returns a coroutine despite is_async being unset — defensive),
    * coerces a non-str result to ``str``,
    * catches ANY exception and returns ``"Error: <tool> failed: <msg>"``
      (never raises — a tool error must not crash the turn).

    The parameter name ``raw_arguments`` is required by the framework's binder.
    """
    handler = entry.handler
    name = entry.name
    is_async = entry.is_async

    async def _run(raw_arguments: dict) -> str:
        try:
            args = raw_arguments if isinstance(raw_arguments, dict) else {}
            if is_async:
                result = await handler(args)
            else:
                # Offload to a thread pool so blocking network I/O (x_search,
                # vuln_check, discord, image_gen, etc.) doesn't freeze the
                # asyncio event loop — and therefore TTS/STT/barge-in — for
                # the duration of the call.
                result = await asyncio.to_thread(handler, args)
                # Defensive: a sync-declared handler that returns a coroutine
                # (e.g. someone forgot is_async=True) is still awaited rather
                # than str()'d into "<coroutine object ...>".
                if inspect.isawaitable(result):
                    result = await result
            if isinstance(result, str):
                return result
            if result is None:
                return ""
            return str(result)
        except Exception as exc:  # noqa: BLE001 — a tool error must not crash the turn
            logger.warning("Tool %s raised %s: %s", name, type(exc).__name__, exc)
            return f"Error: {name} failed: {exc}"

    # Give the wrapper a useful __name__ for any framework-side introspection.
    _run.__name__ = f"jarvis_tool_{name}"
    return _run


def to_livekit_tool(entry: ToolEntry) -> RawFunctionTool:
    """Convert a ``ToolEntry`` into a LiveKit ``RawFunctionTool``.

    Builds ``raw_schema = {name, description, parameters}`` (parameters
    sanitized so every object node sets ``additionalProperties: false``) and
    wraps the handler so it runs async/sync, str-coerces, and never raises.
    """
    parameters = sanitize_schema(_extract_parameters(entry))
    description = entry.description or (entry.schema or {}).get("description", "")
    raw_schema = {
        "name": entry.name,
        "description": description,
        "parameters": parameters,
    }
    return function_tool(_build_wrapped_handler(entry), raw_schema=raw_schema)


# ---------------------------------------------------------------------------
# Bulk load
# ---------------------------------------------------------------------------


def load_all_livekit_tools(tools_dir=None) -> List[RawFunctionTool]:
    """Discover all self-registering tools and adapt them to RawFunctionTools.

    Runs AST discovery (importing each tool module so its ``registry.register``
    side effect fires) and plugin discovery (importing each plugin's
    ``register(ctx)`` so plugin-contributed tools land in the registry too),
    then adapts every registered ``ToolEntry``. Skips, with a logged warning,
    any entry whose ``check_fn`` currently returns False, and any entry that
    fails to adapt (so one malformed schema can't break the rest).
    """
    discover_builtin_tools(tools_dir)

    # Plugins contribute tools through PluginContext.register_tool ->
    # registry.register, so they must run before we snapshot all_entries().
    # Idempotent (cached after first run) and isolated: a broken plugin must
    # not take down the built-in tool surface.
    try:
        from .plugin_system import discover_plugins

        discover_plugins()
    except Exception as exc:  # noqa: BLE001 — plugin failure must not break tools
        logger.warning("Plugin discovery failed (built-in tools unaffected): %s", exc)

    # MCP servers (configured in ~/.jarvis/mcp.json) register their discovered
    # tools dynamically — there is no static ``registry.register(...)`` for the
    # AST walk to find, so trigger discovery explicitly here, before the
    # all_entries() snapshot. Inert (returns immediately) when the config file
    # is absent/empty or the optional ``mcp`` SDK is not installed. Isolated:
    # a server connection failure must not take down the built-in surface.
    try:
        from .mcp_client import discover_mcp_tools

        discover_mcp_tools()
    except Exception as exc:  # noqa: BLE001 — MCP failure must not break tools
        logger.warning("MCP discovery failed (built-in tools unaffected): %s", exc)

    # Local-mode core subset (2026-07-11): computed ONCE per load. None in
    # cloud mode → the loop below is byte-identical to the pre-filter code.
    local_allow = local_voice_tool_allowlist()
    local_dropped: List[str] = []

    tools: List[RawFunctionTool] = []
    kept_names: List[str] = []
    for entry in registry.all_entries():
        if entry.check_fn is not None and not registry.is_available(entry.name):
            logger.warning("Skipping tool %s — check_fn returned False (unavailable)", entry.name)
            continue
        # Conversation-mode tool allowlist (on top of check_fn availability).
        # When the active mode restricts tools, skip any not in its allowlist
        # (CORE_TOOLS always pass). No active restriction → no-op.
        from pipeline.conversation_modes import tool_is_mode_allowed
        if not tool_is_mode_allowed(entry.name):
            logger.info("Skipping tool %s — not in the active conversation mode's allowlist", entry.name)
            continue
        if local_allow is not None and entry.name not in local_allow:
            local_dropped.append(entry.name)
            continue
        try:
            tools.append(to_livekit_tool(entry))
            kept_names.append(entry.name)
        except Exception as exc:  # noqa: BLE001 — one bad tool must not break the surface
            logger.warning("Skipping tool %s — failed to adapt: %s", entry.name, exc)
            continue
    if local_allow is not None:
        logger.info(
            "[local] voice-mode=local trimmed the supervisor tool surface to "
            "%d core tools: %s (dropped %d big-schema tools; tune via "
            "JARVIS_LOCAL_VOICE_TOOLS or tools/_adapter.py::LOCAL_VOICE_CORE_TOOLS)",
            len(kept_names), ", ".join(sorted(kept_names)), len(local_dropped),
        )
    return tools
