"""Adapter: Hermes ``ToolEntry`` → LiveKit 1.5.x ``RawFunctionTool``.

The keystone of the port. Hermes declares a tool as a JSON schema + a handler.
LiveKit's ``function_tool(handler, raw_schema=<dict>)`` turns a raw schema +
handler into a ``RawFunctionTool`` the voice supervisor can register and call.
This module bridges the two:

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
we don't declare one (the ported Hermes handlers don't take it).
"""
from __future__ import annotations

import inspect
import logging
from typing import Any, List

from livekit.agents.llm import RawFunctionTool, function_tool

from .registry import ToolEntry, discover_builtin_tools, registry

logger = logging.getLogger(__name__)

__all__ = [
    "to_livekit_tool",
    "load_all_livekit_tools",
    "sanitize_schema",
]


# ---------------------------------------------------------------------------
# Schema sanitizer
#
# Mirrors sanitizers/anthropic_strict_schema.py::fix_schema. Anthropic's
# /v1/messages rejects tool definitions whose object-typed nodes don't set
# `additionalProperties: false`. We apply it at adapt time (rather than relying
# solely on the import-time monkey-patch) so the RawFunctionTool a ported tool
# produces is correct at the source — belt-and-suspenders with the patch, and
# correct even for non-Anthropic providers (they tolerate the extra key).
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

    Hermes schemas store the parameters under ``schema["parameters"]``. Some
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
                result = handler(args)
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
    """Convert a Hermes ``ToolEntry`` into a LiveKit ``RawFunctionTool``.

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
    side effect fires), then adapts every registered ``ToolEntry``. Skips, with
    a logged warning, any entry whose ``check_fn`` currently returns False, and
    any entry that fails to adapt (so one malformed schema can't break the rest).
    """
    discover_builtin_tools(tools_dir)

    tools: List[RawFunctionTool] = []
    for entry in registry.all_entries():
        if entry.check_fn is not None and not registry.is_available(entry.name):
            logger.warning("Skipping tool %s — check_fn returned False (unavailable)", entry.name)
            continue
        try:
            tools.append(to_livekit_tool(entry))
        except Exception as exc:  # noqa: BLE001 — one bad tool must not break the surface
            logger.warning("Skipping tool %s — failed to adapt: %s", entry.name, exc)
            continue
    return tools
