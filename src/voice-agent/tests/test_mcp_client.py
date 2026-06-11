"""Tests for the minimal MCP client (tools/mcp_client.py).

Proves:
  (a) inert with NO ~/.jarvis/mcp.json — no tools registered, no servers, []
  (b) config parsing: {"servers": {...}} and bare {name: spec} both work;
      malformed / no-command-no-url entries are skipped, not fatal
  (c) discovery registers each discovered tool from a MOCKED server into the
      registry under mcp__<server>__<tool>, with a working proxy handler
  (d) a per-server connection FAILURE is caught — that server registers no
      tools and the error is recorded, without crashing discovery
  (e) the JSON-Schema normalizer + name sanitizer behave (pure-function units)

NO real network. NO real subprocess MCP servers. The MCP session is a fake.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

# Ensure the voice-agent root is importable.
_VA_ROOT = Path(__file__).resolve().parent.parent
if str(_VA_ROOT) not in sys.path:
    sys.path.insert(0, str(_VA_ROOT))


@pytest.fixture(autouse=True)
def _isolated_jarvis_home(tmp_path, monkeypatch):
    """Point JARVIS_HOME at a tmp dir (so no real ~/.jarvis/mcp.json is read)
    and tear down any module state a test leaves behind."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    import tools.mcp_client as mc

    mc.shutdown_mcp_servers()
    yield tmp_path
    mc.shutdown_mcp_servers()


def _write_config(home: Path, data: dict) -> None:
    (home / "mcp.json").write_text(json.dumps(data), encoding="utf-8")


# ---------------------------------------------------------------------------
# Fake MCP objects (no SDK session, no transport)
# ---------------------------------------------------------------------------


def _fake_tool(name: str, description: str = "", input_schema: dict | None = None):
    return SimpleNamespace(
        name=name,
        description=description,
        inputSchema=input_schema if input_schema is not None else {"type": "object", "properties": {}},
    )


def _fake_call_result(*, text: str = "", structured=None, is_error: bool = False):
    content = [SimpleNamespace(text=text)] if text else []
    return SimpleNamespace(content=content, structuredContent=structured, isError=is_error)


class _FakeServer:
    """Stand-in for tools.mcp_client.MCPServer with a live, connected session."""

    def __init__(self, name, config, tools, *, call_result=None, call_raises=None):
        self.name = name
        self.config = config
        self.tools = tools
        self.error = None
        self.tool_timeout = 30.0
        self.session = object()  # truthy → "connected"
        self._call_result = call_result or _fake_call_result(text="ok")
        self._call_raises = call_raises
        self._shutdown = None

    def _is_http(self):
        return bool(self.config.get("url"))

    async def _call_tool_async(self, tool_name, args):
        if self._call_raises is not None:
            raise self._call_raises
        r = self._call_result
        if getattr(r, "isError", False):
            return json.dumps({"error": "boom"}, ensure_ascii=False)
        text = "".join(getattr(b, "text", "") for b in (r.content or []))
        return json.dumps({"result": text, "_echo_args": args, "_tool": tool_name}, ensure_ascii=False)


# ---------------------------------------------------------------------------
# (a) inert without config
# ---------------------------------------------------------------------------


class TestInertWithoutConfig:
    def test_no_config_file_returns_empty(self):
        import tools.mcp_client as mc

        assert mc.load_mcp_config() == {}
        assert mc.discover_mcp_tools() == []
        assert mc.get_mcp_status() == []

    def test_no_loop_thread_started_when_inert(self, _isolated_jarvis_home):
        import tools.mcp_client as mc

        # Order-independent: if an earlier test in the session ran
        # discovery against a usable config (any load_all_livekit_tools
        # call with a real ~/.jarvis/mcp.json does), the daemon loop
        # already exists and is never torn down (by design — see
        # shutdown_mcp_servers). What THIS test guards: discovery with
        # no config must not START a loop, i.e. the loop state must be
        # exactly what it was before the call.
        before = mc._loop
        mc.discover_mcp_tools()  # no config → must not start a loop
        assert mc._loop is before

    def test_empty_config_file_is_inert(self, _isolated_jarvis_home):
        import tools.mcp_client as mc

        (_isolated_jarvis_home / "mcp.json").write_text("", encoding="utf-8")
        assert mc.load_mcp_config() == {}
        assert mc.discover_mcp_tools() == []

    def test_malformed_config_is_inert(self, _isolated_jarvis_home):
        import tools.mcp_client as mc

        (_isolated_jarvis_home / "mcp.json").write_text("{not json", encoding="utf-8")
        assert mc.load_mcp_config() == {}
        assert mc.discover_mcp_tools() == []

    def test_no_mcp_tools_in_surface_without_config(self):
        from tools._adapter import load_all_livekit_tools

        names = [t.info.name for t in load_all_livekit_tools()]
        assert not [n for n in names if n.startswith("mcp__")]


# ---------------------------------------------------------------------------
# (b) config parsing
# ---------------------------------------------------------------------------


class TestConfigParsing:
    def test_servers_wrapper(self, _isolated_jarvis_home):
        import tools.mcp_client as mc

        _write_config(_isolated_jarvis_home, {"servers": {"fs": {"command": "x"}}})
        cfg = mc.load_mcp_config()
        assert set(cfg) == {"fs"}

    def test_bare_mapping(self, _isolated_jarvis_home):
        import tools.mcp_client as mc

        _write_config(_isolated_jarvis_home, {"remote": {"url": "https://h/mcp"}})
        cfg = mc.load_mcp_config()
        assert set(cfg) == {"remote"}

    def test_skips_entries_without_command_or_url(self, _isolated_jarvis_home):
        import tools.mcp_client as mc

        _write_config(
            _isolated_jarvis_home,
            {"servers": {"good": {"command": "x"}, "bad": {"note": "nothing usable"}}},
        )
        cfg = mc.load_mcp_config()
        assert set(cfg) == {"good"}

    def test_skips_disabled_entries(self, _isolated_jarvis_home):
        import tools.mcp_client as mc

        _write_config(
            _isolated_jarvis_home,
            {"servers": {"on": {"command": "x"}, "off": {"command": "y", "disabled": True}}},
        )
        cfg = mc.load_mcp_config()
        assert set(cfg) == {"on"}


# ---------------------------------------------------------------------------
# (c) discovery registers MOCKED tools + proxy handler works
# ---------------------------------------------------------------------------


class TestDiscoveryRegistersTools:
    def test_registers_namespaced_tools(self, _isolated_jarvis_home):
        import tools.mcp_client as mc
        from tools.registry import registry

        _write_config(_isolated_jarvis_home, {"servers": {"myserver": {"command": "x"}}})

        tools = [
            _fake_tool("search", "Search the web", {"type": "object", "properties": {"q": {"type": "string"}}}),
            _fake_tool("fetch", "Fetch a URL"),
        ]

        def _fake_connect(name, spec):
            return _FakeServer(name, spec, tools)

        with patch.object(mc, "_connect_server", _fake_connect):
            registered = mc.discover_mcp_tools()

        assert set(registered) == {"mcp__myserver__search", "mcp__myserver__fetch"}
        entry = registry.get_entry("mcp__myserver__search")
        assert entry is not None
        assert entry.toolset == "mcp"
        # Description carries provenance + the param schema is preserved.
        assert "[MCP:myserver]" in entry.description
        assert entry.schema["parameters"]["properties"] == {"q": {"type": "string"}}

    def test_registered_tools_appear_in_livekit_surface(self, _isolated_jarvis_home):
        import tools.mcp_client as mc
        from tools._adapter import load_all_livekit_tools

        _write_config(_isolated_jarvis_home, {"servers": {"srv": {"command": "x"}}})
        tools = [_fake_tool("ping")]

        with patch.object(mc, "_connect_server", lambda n, s: _FakeServer(n, s, tools)):
            mc.discover_mcp_tools()
            names = [t.info.name for t in load_all_livekit_tools()]

        assert "mcp__srv__ping" in names

    def test_proxy_handler_invokes_server(self, _isolated_jarvis_home):
        import tools.mcp_client as mc
        from tools.registry import registry

        _write_config(_isolated_jarvis_home, {"servers": {"srv": {"command": "x"}}})
        server = _FakeServer("srv", {"command": "x"}, [_fake_tool("echo")],
                             call_result=_fake_call_result(text="pong"))

        with patch.object(mc, "_connect_server", lambda n, s: server):
            mc.discover_mcp_tools()

        # Register the fake server into module state so the handler resolves it.
        with mc._lock:
            mc._servers["srv"] = server

        handler = registry.get_entry("mcp__srv__echo").handler
        out = json.loads(handler({"message": "hi"}))
        assert out["result"] == "pong"
        assert out["_echo_args"] == {"message": "hi"}
        assert out["_tool"] == "echo"

    def test_handler_reports_disconnected_server(self, _isolated_jarvis_home):
        import tools.mcp_client as mc

        handler = mc._make_tool_handler("ghost", "tool")
        out = json.loads(handler({}))
        assert "not connected" in out["error"]

    def test_idempotent_second_discovery(self, _isolated_jarvis_home):
        import tools.mcp_client as mc

        _write_config(_isolated_jarvis_home, {"servers": {"srv": {"command": "x"}}})
        calls = {"n": 0}

        def _count(n, s):
            calls["n"] += 1
            return _FakeServer(n, s, [_fake_tool("t")])

        with patch.object(mc, "_connect_server", _count):
            first = mc.discover_mcp_tools()
            second = mc.discover_mcp_tools()  # already-connected → no reconnect

        assert first == second == ["mcp__srv__t"]
        assert calls["n"] == 1


# ---------------------------------------------------------------------------
# (d) per-server failure is caught, not fatal
# ---------------------------------------------------------------------------


class TestPerServerFailureIsolation:
    def test_failed_server_registers_nothing_but_does_not_crash(self, _isolated_jarvis_home):
        import tools.mcp_client as mc

        _write_config(_isolated_jarvis_home, {"servers": {"broken": {"command": "x"}}})

        def _failed(name, spec):
            s = _FakeServer(name, spec, [])
            s.session = None
            s.error = "ConnectionError: refused"
            return s

        with patch.object(mc, "_connect_server", _failed):
            registered = mc.discover_mcp_tools()

        assert registered == []
        status = mc.get_mcp_status()
        assert status and status[0]["connected"] is False
        assert status[0]["error"] == "ConnectionError: refused"

    def test_one_server_fails_other_succeeds(self, _isolated_jarvis_home):
        import tools.mcp_client as mc

        _write_config(
            _isolated_jarvis_home,
            {"servers": {"good": {"command": "x"}, "bad": {"command": "y"}}},
        )

        def _mixed(name, spec):
            if name == "bad":
                s = _FakeServer(name, spec, [])
                s.session = None
                s.error = "boom"
                return s
            return _FakeServer(name, spec, [_fake_tool("works")])

        with patch.object(mc, "_connect_server", _mixed):
            registered = mc.discover_mcp_tools()

        assert registered == ["mcp__good__works"]

    def test_connect_raising_does_not_abort_discovery(self, _isolated_jarvis_home):
        import tools.mcp_client as mc

        _write_config(
            _isolated_jarvis_home,
            {"servers": {"raiser": {"command": "x"}, "ok": {"command": "y"}}},
        )

        def _maybe_raise(name, spec):
            if name == "raiser":
                raise RuntimeError("connect blew up")
            return _FakeServer(name, spec, [_fake_tool("safe")])

        with patch.object(mc, "_connect_server", _maybe_raise):
            registered = mc.discover_mcp_tools()

        assert registered == ["mcp__ok__safe"]


# ---------------------------------------------------------------------------
# (e) pure-function units: name sanitizer + schema normalizer
# ---------------------------------------------------------------------------


class TestNameSanitizer:
    def test_replaces_unsafe_chars(self):
        import tools.mcp_client as mc

        assert mc.sanitize_mcp_name_component("my-server.v2") == "my_server_v2"
        assert mc.make_tool_name("a b", "do/thing") == "mcp__a_b__do_thing"


class TestSchemaNormalizer:
    def test_none_schema_becomes_empty_object(self):
        import tools.mcp_client as mc

        assert mc._normalize_mcp_input_schema(None) == {"type": "object", "properties": {}}

    def test_object_missing_properties_filled(self):
        import tools.mcp_client as mc

        out = mc._normalize_mcp_input_schema({"type": "object"})
        assert out["properties"] == {}

    def test_required_pruned_to_existing_properties(self):
        import tools.mcp_client as mc

        out = mc._normalize_mcp_input_schema(
            {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a", "ghost"]}
        )
        assert out["required"] == ["a"]

    def test_definitions_rewritten_to_defs(self):
        import tools.mcp_client as mc

        out = mc._normalize_mcp_input_schema(
            {
                "type": "object",
                "properties": {"x": {"$ref": "#/definitions/Foo"}},
                "definitions": {"Foo": {"type": "string"}},
            }
        )
        assert "$defs" in out
        assert out["properties"]["x"]["$ref"] == "#/$defs/Foo"

    def test_nullable_union_collapsed(self):
        import tools.mcp_client as mc

        out = mc._normalize_mcp_input_schema(
            {
                "type": "object",
                "properties": {
                    "name": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                },
            }
        )
        prop = out["properties"]["name"]
        assert prop.get("type") == "string"
        assert prop.get("nullable") is True
        assert "anyOf" not in prop
