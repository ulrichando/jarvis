"""Tests for the JARVIS tool-framework layer (foundation wave).

Proves the keystone path:
  ToolEntry (schema + handler) -> to_livekit_tool -> RawFunctionTool
that the JARVIS supervisor can register, with:
  (a) correct .info.name and raw_schema shape,
  (b) wrapped handler runs for async + sync handlers, str-coerces, and
      catches exceptions (a tool error must never crash the turn),
  (c) the schema sanitizer sets additionalProperties:false on every
      nested object node (Anthropic supervisor hard requirement),
  (d) AST discovery finds the temporary _demo_tool and
      load_all_livekit_tools() returns it adapted.

Mirrors the sys.path / asyncio patterns used by the rest of tests/.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

# Make the voice-agent package root importable (so `import tools...` works)
# regardless of pytest's rootdir, mirroring the other test modules.
_VOICE_AGENT_ROOT = Path(__file__).resolve().parent.parent
if str(_VOICE_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_VOICE_AGENT_ROOT))

from livekit.agents.llm import is_raw_function_tool  # noqa: E402

from tools import _adapter as adapter  # noqa: E402
from tools.registry import ToolEntry, registry  # noqa: E402


def _run(coro):
    """Run a coroutine to completion on a throwaway event loop."""
    return asyncio.new_event_loop().run_until_complete(coro)


def _invoke(tool, args: dict):
    """Invoke a RawFunctionTool the way the framework does: the wrapped
    callable takes a `raw_arguments` keyword. Always returns the awaited
    result as the wrapped handler is async."""
    return _run(tool(raw_arguments=args))


# ── (a) ToolEntry -> RawFunctionTool with correct name/shape ──────────


def test_to_livekit_tool_yields_raw_function_tool_with_name():
    entry = ToolEntry(
        name="unit_echo",
        toolset="builtin",
        schema={
            "description": "Echo back the text.",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        },
        handler=lambda raw_arguments: raw_arguments.get("text", ""),
        check_fn=None,
        requires_env=[],
        is_async=False,
        description="Echo back the text.",
        emoji="",
    )
    tool = adapter.to_livekit_tool(entry)
    assert is_raw_function_tool(tool)
    assert tool.info.name == "unit_echo"
    assert tool.info.raw_schema["name"] == "unit_echo"
    assert tool.info.raw_schema["description"] == "Echo back the text."
    assert tool.info.raw_schema["parameters"]["properties"]["text"]["type"] == "string"


def test_description_falls_back_to_schema_description():
    entry = ToolEntry(
        name="desc_fallback",
        toolset="builtin",
        schema={"description": "from schema", "parameters": {"type": "object", "properties": {}}},
        handler=lambda raw_arguments: "x",
        check_fn=None,
        requires_env=[],
        is_async=False,
        description="",  # empty -> must fall back to schema["description"]
        emoji="",
    )
    tool = adapter.to_livekit_tool(entry)
    assert tool.info.raw_schema["description"] == "from schema"


# ── (b) wrapped handler: async + sync + str-coerce + error-catch ──────


def test_wrapped_handler_runs_sync_and_coerces_to_str():
    entry = ToolEntry(
        name="sync_int",
        toolset="builtin",
        schema={"description": "returns int", "parameters": {"type": "object", "properties": {}}},
        handler=lambda raw_arguments: 42,  # non-str return
        check_fn=None,
        requires_env=[],
        is_async=False,
        description="returns int",
        emoji="",
    )
    tool = adapter.to_livekit_tool(entry)
    result = _invoke(tool, {})
    assert result == "42"
    assert isinstance(result, str)


def test_wrapped_handler_runs_async():
    async def _ahandler(raw_arguments):
        return "async:" + raw_arguments.get("v", "")

    entry = ToolEntry(
        name="async_ok",
        toolset="builtin",
        schema={"description": "async", "parameters": {"type": "object", "properties": {"v": {"type": "string"}}}},
        handler=_ahandler,
        check_fn=None,
        requires_env=[],
        is_async=True,
        description="async",
        emoji="",
    )
    tool = adapter.to_livekit_tool(entry)
    assert _invoke(tool, {"v": "hi"}) == "async:hi"


def test_wrapped_handler_catches_sync_exception():
    def _boom(raw_arguments):
        raise RuntimeError("kaboom")

    entry = ToolEntry(
        name="sync_boom",
        toolset="builtin",
        schema={"description": "boom", "parameters": {"type": "object", "properties": {}}},
        handler=_boom,
        check_fn=None,
        requires_env=[],
        is_async=False,
        description="boom",
        emoji="",
    )
    tool = adapter.to_livekit_tool(entry)
    result = _invoke(tool, {})
    assert isinstance(result, str)
    assert result.startswith("Error:")
    assert "sync_boom" in result
    assert "kaboom" in result


def test_wrapped_handler_catches_async_exception():
    async def _aboom(raw_arguments):
        raise ValueError("async-fail")

    entry = ToolEntry(
        name="async_boom",
        toolset="builtin",
        schema={"description": "aboom", "parameters": {"type": "object", "properties": {}}},
        handler=_aboom,
        check_fn=None,
        requires_env=[],
        is_async=True,
        description="aboom",
        emoji="",
    )
    tool = adapter.to_livekit_tool(entry)
    result = _invoke(tool, {})
    assert isinstance(result, str)
    assert result.startswith("Error:")
    assert "async_boom" in result
    assert "async-fail" in result


# ── (c) schema sanitizer sets additionalProperties:false everywhere ───


def test_sanitizer_sets_additional_properties_false_on_nested_objects():
    schema = {
        "type": "object",
        "properties": {
            "outer": {
                "type": "object",
                "properties": {
                    "inner": {"type": "object", "properties": {"a": {"type": "string"}}},
                },
            },
            "arr": {
                "type": "array",
                "items": {"type": "object", "properties": {"b": {"type": "number"}}},
            },
            "branch": {
                "anyOf": [
                    {"type": "object", "properties": {"c": {"type": "string"}}},
                    {"type": "string"},
                ],
            },
        },
    }
    out = adapter.sanitize_schema(schema)
    # top-level
    assert out["additionalProperties"] is False
    # nested object
    assert out["properties"]["outer"]["additionalProperties"] is False
    assert out["properties"]["outer"]["properties"]["inner"]["additionalProperties"] is False
    # array items object
    assert out["properties"]["arr"]["items"]["additionalProperties"] is False
    # anyOf branch object (and not the string branch)
    assert out["properties"]["branch"]["anyOf"][0]["additionalProperties"] is False
    assert "additionalProperties" not in out["properties"]["branch"]["anyOf"][1]


def test_adapter_applies_sanitizer_to_emitted_raw_schema():
    entry = ToolEntry(
        name="nested_obj_tool",
        toolset="builtin",
        schema={
            "description": "nested",
            "parameters": {
                "type": "object",
                "properties": {"cfg": {"type": "object", "properties": {"k": {"type": "string"}}}},
            },
        },
        handler=lambda raw_arguments: "ok",
        check_fn=None,
        requires_env=[],
        is_async=False,
        description="nested",
        emoji="",
    )
    tool = adapter.to_livekit_tool(entry)
    params = tool.info.raw_schema["parameters"]
    assert params["additionalProperties"] is False
    assert params["properties"]["cfg"]["additionalProperties"] is False


# ── (d) discovery finds _demo_tool; load_all_livekit_tools adapts it ──


def test_discover_builtin_tools_finds_demo_tool():
    imported = registry_discover()
    assert any(m.endswith("_demo_tool") for m in imported), imported
    assert registry.get_entry("echo_demo") is not None


def test_load_all_livekit_tools_returns_demo_adapted():
    tools = adapter.load_all_livekit_tools()
    assert all(is_raw_function_tool(t) for t in tools)
    names = {t.info.name for t in tools}
    assert "echo_demo" in names
    # The adapted demo tool must actually run end-to-end.
    demo = next(t for t in tools if t.info.name == "echo_demo")
    assert _invoke(demo, {"text": "ping"}) == "ping"


def test_load_skips_entries_whose_check_fn_is_false():
    registry.register(
        name="unit_unavailable",
        toolset="builtin",
        schema={"description": "never", "parameters": {"type": "object", "properties": {}}},
        handler=lambda raw_arguments: "nope",
        check_fn=lambda: False,
        is_async=False,
    )
    try:
        tools = adapter.load_all_livekit_tools()
        names = {t.info.name for t in tools}
        assert "unit_unavailable" not in names
    finally:
        registry.deregister("unit_unavailable")


# ── helper: call discover via the registry module's public fn ─────────


def registry_discover():
    from tools.registry import discover_builtin_tools

    return discover_builtin_tools()
