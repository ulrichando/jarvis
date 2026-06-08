"""Anthropic strict-schema fixup for tool definitions.

Anthropic's /v1/messages endpoint (2026-05) rejects tool definitions
whose nested `type: object` properties don't set `additionalProperties:
false`. The livekit-plugins-anthropic plugin's schema generator handles
the OUTER object correctly under `_strict_tool_schema=True` but leaves
nested objects with `additionalProperties: true`, and the loose mode
(`_strict_tool_schema=False`) doesn't help either — same nested
violation.

JARVIS's tool surface includes a few @function_tool entries that accept
dict-typed parameters (e.g. `ext_fill_form(fields)`, computer-use
`drag(args)` shapes). Those expand into JSON Schema objects whose
nested type definitions trip the validator.

This module monkey-patches the Anthropic LLM's `chat()` to walk
`extra["tools"]` post-build and recursively set
`additionalProperties: false` on every object schema, anywhere in the
tree (input_schema, anyOf/oneOf branches, properties values, items
of arrays, $defs, etc.). Idempotent — re-installing is a no-op.

Live failure 2026-05-11: every Anthropic supervisor turn returned 400
'tools.0.custom: For \\'object\\' type, additionalProperties must be
explicitly set to false', leaving the agent silent. Installing this
patch at import time fixes the round-trip.
"""
from __future__ import annotations

import logging
from typing import Any


__all__ = ["install", "fix_schema"]


logger = logging.getLogger("jarvis.anthropic_strict_schema")


_INSTALLED = False


def fix_schema(node: Any) -> Any:
    """Recursively walk a JSON Schema node and force every `type: object`
    sub-tree to declare `additionalProperties: false`. Mutates in place
    and also returns the same reference so callers can chain.

    Handles: top-level objects, nested object properties, array `items`,
    `anyOf` / `oneOf` / `allOf` branches, `$defs` / `definitions`.
    """
    if isinstance(node, list):
        for item in node:
            fix_schema(item)
        return node

    if not isinstance(node, dict):
        return node

    # Detect object-type schemas. The JSON-Schema-y way: type == "object",
    # OR type is a list containing "object". Some Pydantic-emitted
    # schemas use `"type": ["object", "null"]` for Optional[dict] params.
    t = node.get("type")
    is_object = (
        t == "object"
        or (isinstance(t, list) and "object" in t)
        # Some schemas leave `type` implicit but carry `properties` —
        # treat those as objects too.
        or ("properties" in node and t is None)
    )

    if is_object and node.get("additionalProperties") is not False:
        node["additionalProperties"] = False

    # Recurse into all the standard JSON-Schema containers.
    for key in (
        "properties",
        "patternProperties",
        "$defs",
        "definitions",
    ):
        sub = node.get(key)
        if isinstance(sub, dict):
            for v in sub.values():
                fix_schema(v)

    for key in ("items", "contains", "not", "if", "then", "else", "additionalItems"):
        sub = node.get(key)
        if sub is not None:
            fix_schema(sub)

    for key in ("anyOf", "oneOf", "allOf", "prefixItems"):
        sub = node.get(key)
        if isinstance(sub, list):
            for v in sub:
                fix_schema(v)

    return node


def install() -> None:
    """Monkey-patch ToolContext.parse_function_tools so every Anthropic
    tool schema gets `additionalProperties: false` on every object node
    before the request is built.

    We hook parse_function_tools rather than the Anthropic LLM's chat()
    because by the time chat() returns, the tools payload is already
    baked into the messages.create partial — too late to mutate.

    Idempotent — repeat calls return without re-patching.
    """
    global _INSTALLED
    if _INSTALLED:
        return
    try:
        from livekit.agents.llm import ToolContext
    except Exception as e:
        logger.warning(
            f"[anthropic-strict-schema] ToolContext import failed — patch NOT installed: {e}. "
            f"All Anthropic tool calls will return 400 until this is resolved."
        )
        # Do NOT set _INSTALLED = True — the patch did nothing.
        # Leaving it False means a future call to install() will retry,
        # and the loud warning will appear on every startup until fixed.
        return

    orig_parse = ToolContext.parse_function_tools

    def _patched_parse(self, fmt: str, *args, **kw):
        schemas = orig_parse(self, fmt, *args, **kw)
        if fmt == "anthropic" and isinstance(schemas, list):
            for s in schemas:
                for k in ("input_schema", "parameters"):
                    if k in s:
                        fix_schema(s[k])
        return schemas

    ToolContext.parse_function_tools = _patched_parse  # type: ignore[assignment]
    _INSTALLED = True
    logger.info(
        "[anthropic-strict-schema] installed: every Anthropic tool schema "
        "will get additionalProperties=false on every object node"
    )
