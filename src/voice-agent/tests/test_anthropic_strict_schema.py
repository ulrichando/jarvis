"""Verify sanitizers.anthropic_strict_schema forces every nested
object in an Anthropic tool schema to declare `additionalProperties:
false`.

Live failure 2026-05-11: Claude Haiku 4.5 was wired into both the
SPEECH_MODELS picker and the FallbackAdapter rung-3 slot. Every
supervisor turn that reached Anthropic returned HTTP 400
`tools.0.custom: For 'object' type, additionalProperties must be
explicitly set to false`, leaving the agent silent.

Root cause: `strict_schema_relax` patches `build_strict_openai_schema`
to always return a legacy schema, which is fine for Groq (legacy
doesn't add `additionalProperties` anywhere — Groq accepts) but a
hard reject for Anthropic, which validates that every object node
explicitly sets the flag to `false`. Anthropic's loose mode
(`_strict_tool_schema=False`) also fails for the same reason; the
legacy schema simply has no `additionalProperties` anywhere.

Fix: walk the schema produced by `parse_function_tools("anthropic",
...)` and recursively set `additionalProperties: false` on every
object node, anywhere in the tree (top-level, nested `properties`
values, `items`, `anyOf`/`oneOf` branches, `$defs`, etc.). These
tests pin the recursion and the integration with the live tool set.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest


def test_fix_schema_sets_additional_properties_false_on_top_level():
    """A bare `type: object` node must get additionalProperties=false."""
    from sanitizers.anthropic_strict_schema import fix_schema
    node = {"type": "object", "properties": {"x": {"type": "string"}}}
    fix_schema(node)
    assert node["additionalProperties"] is False


def test_fix_schema_recurses_into_nested_object_properties():
    """A dict-typed param expands to `type: object` — the sanitizer
    must reach it and set the flag."""
    from sanitizers.anthropic_strict_schema import fix_schema
    node = {
        "type": "object",
        "properties": {
            "fields": {"type": "object", "additionalProperties": True},
        },
    }
    fix_schema(node)
    assert node["additionalProperties"] is False
    assert node["properties"]["fields"]["additionalProperties"] is False


def test_fix_schema_handles_anyof_with_object_branch():
    """Pydantic emits `anyOf: [{"type": "object", ...}, {"type": "null"}]`
    for Optional[dict]. The object branch must get the flag."""
    from sanitizers.anthropic_strict_schema import fix_schema
    node = {
        "type": "object",
        "properties": {
            "maybe": {
                "anyOf": [
                    {"type": "object", "properties": {"k": {"type": "string"}}},
                    {"type": "null"},
                ],
            },
        },
    }
    fix_schema(node)
    branch = node["properties"]["maybe"]["anyOf"][0]
    assert branch["additionalProperties"] is False
    assert "additionalProperties" not in node["properties"]["maybe"]["anyOf"][1]


def test_fix_schema_handles_array_items_object():
    """`items: { type: object }` is the schema for `list[dict]` params."""
    from sanitizers.anthropic_strict_schema import fix_schema
    node = {
        "type": "object",
        "properties": {
            "rows": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"id": {"type": "integer"}},
                },
            },
        },
    }
    fix_schema(node)
    assert node["additionalProperties"] is False
    assert node["properties"]["rows"]["items"]["additionalProperties"] is False


def test_fix_schema_walks_defs():
    """Pydantic emits nested types as `$defs` references — the sanitizer
    must reach inside `$defs` and set the flag on every referenced shape."""
    from sanitizers.anthropic_strict_schema import fix_schema
    node = {
        "type": "object",
        "properties": {"a": {"$ref": "#/$defs/Inner"}},
        "$defs": {
            "Inner": {"type": "object", "properties": {"k": {"type": "string"}}},
        },
    }
    fix_schema(node)
    assert node["$defs"]["Inner"]["additionalProperties"] is False


def test_fix_schema_object_type_via_type_list():
    """Some schemas use `type: ["object", "null"]` for Optional[dict]."""
    from sanitizers.anthropic_strict_schema import fix_schema
    node = {"type": ["object", "null"], "properties": {"x": {"type": "string"}}}
    fix_schema(node)
    assert node["additionalProperties"] is False


def test_fix_schema_implicit_object_from_properties():
    """Properties without an explicit `type` should still be treated as
    objects."""
    from sanitizers.anthropic_strict_schema import fix_schema
    node = {"properties": {"x": {"type": "string"}}}
    fix_schema(node)
    assert node["additionalProperties"] is False


def test_fix_schema_does_not_touch_non_object_nodes():
    """Scalars must be left alone."""
    from sanitizers.anthropic_strict_schema import fix_schema
    node = {"type": "string", "description": "a string"}
    fix_schema(node)
    assert "additionalProperties" not in node


def test_fix_schema_preserves_explicit_false():
    """If a caller already set additionalProperties:false, leave it."""
    from sanitizers.anthropic_strict_schema import fix_schema
    node = {"type": "object", "additionalProperties": False}
    fix_schema(node)
    assert node["additionalProperties"] is False


def test_fix_schema_is_idempotent_on_value():
    """Calling fix_schema twice yields the same shape."""
    from sanitizers.anthropic_strict_schema import fix_schema
    import copy
    node = {
        "type": "object",
        "properties": {"fields": {"type": "object"}},
    }
    fix_schema(node)
    snapshot = copy.deepcopy(node)
    fix_schema(node)
    assert node == snapshot


def test_install_patches_parse_function_tools():
    """After install(), parse_function_tools('anthropic', ...) returns
    schemas with additionalProperties:false everywhere."""
    from livekit.agents import llm
    import sanitizers.strict_schema_relax as ssr
    import sanitizers.anthropic_strict_schema as anth_ss
    ssr.install()
    anth_ss.install()

    @llm.function_tool
    async def fake_tool(fields: dict, label: str = "x") -> str:
        """A tool with a dict-typed param and a defaulted string."""
        return ""

    tc = llm.ToolContext([fake_tool])
    schemas = tc.parse_function_tools("anthropic", strict=True)
    assert len(schemas) == 1
    schema = schemas[0]
    input_schema = schema["input_schema"]

    # Outer object: additionalProperties:false.
    assert input_schema["additionalProperties"] is False, (
        f"outer object missing additionalProperties=false: {input_schema}"
    )
    # Nested `fields` (dict-typed): additionalProperties:false.
    fields = input_schema["properties"]["fields"]
    assert fields["additionalProperties"] is False, (
        f"nested 'fields' object missing additionalProperties=false: {fields}"
    )


def test_install_works_for_strict_false_path():
    """The patch must also fire on the strict=False call path
    (build_legacy_openai_schema(internally_tagged=True))."""
    from livekit.agents import llm
    import sanitizers.strict_schema_relax as ssr
    import sanitizers.anthropic_strict_schema as anth_ss
    ssr.install()
    anth_ss.install()

    @llm.function_tool
    async def fake_tool(payload: dict) -> str:
        """A tool with a dict param."""
        return ""

    tc = llm.ToolContext([fake_tool])
    schemas = tc.parse_function_tools("anthropic", strict=False)
    assert schemas[0]["input_schema"]["additionalProperties"] is False
    assert (
        schemas[0]["input_schema"]["properties"]["payload"]["additionalProperties"]
        is False
    )


def test_install_is_idempotent():
    """Re-calling install() must be a no-op."""
    from livekit.agents.llm import ToolContext
    import sanitizers.anthropic_strict_schema as anth_ss
    anth_ss.install()
    first = ToolContext.parse_function_tools
    anth_ss.install()
    second = ToolContext.parse_function_tools
    assert first is second


def test_install_does_not_affect_openai_format():
    """The patch only fixes the anthropic format. Calling
    parse_function_tools('openai', ...) must NOT add
    additionalProperties=false (Groq doesn't want it on every object —
    strict_schema_relax keeps it off for Groq)."""
    from livekit.agents import llm
    import sanitizers.strict_schema_relax as ssr
    import sanitizers.anthropic_strict_schema as anth_ss
    ssr.install()
    anth_ss.install()

    @llm.function_tool
    async def fake_tool(payload: dict) -> str:
        """A tool with a dict param."""
        return ""

    tc = llm.ToolContext([fake_tool])
    openai_schemas = tc.parse_function_tools("openai", strict=True)
    # strict_schema_relax forces legacy shape for openai. The shape
    # depends on how the underlying generator structures things, but
    # the contract we care about: the sanitizer didn't reach into the
    # openai path and add the field. Either: shape has no
    # additionalProperties at all, or it's not False on the nested
    # `payload` object.
    s = openai_schemas[0]
    # Find the parameters block — legacy shape wraps in
    # {"type": "function", "function": {..., "parameters": {...}}}.
    params = s.get("function", {}).get("parameters", s.get("parameters"))
    assert params is not None
    # Legacy shape from strict_schema_relax leaves additionalProperties
    # unset (which JSON-Schema-equivalent to true). The sanitizer
    # must not have touched it.
    nested = (params.get("properties") or {}).get("payload", {})
    # Either the sanitizer didn't run on openai (nested.additionalProperties
    # is missing or != False) — that's what we want.
    assert nested.get("additionalProperties") is not False, (
        "sanitizer should NOT alter openai-format schemas; "
        f"got nested payload schema = {nested}"
    )
