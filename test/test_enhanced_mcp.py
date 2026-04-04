"""Tests for brain.mcp.enhanced_client module."""
import json
import pytest
from brain.mcp.enhanced_client import (
    MCPToolName,
    MCPServerConfig,
    MCPConnection,
    MCPConfigLoader,
    MCPToolProxy,
    MCPHealthChecker,
)


# ---------------------------------------------------------------------------
# MCPToolName
# ---------------------------------------------------------------------------

class TestMCPToolName:
    def test_sanitize_basic(self):
        assert MCPToolName.sanitize_name("my-server") == "my_server"
        assert MCPToolName.sanitize_name("My.Server") == "my_server"
        assert MCPToolName.sanitize_name("hello world") == "hello_world"

    def test_sanitize_collapses_underscores(self):
        assert MCPToolName.sanitize_name("a..b--c") == "a_b_c"
        assert MCPToolName.sanitize_name("__leading__") == "leading"

    def test_sanitize_claude_ai_style(self):
        assert MCPToolName.sanitize_name("claude.ai Figma") == "claude_ai_figma"

    def test_normalize(self):
        assert MCPToolName.normalize("my-server", "do_thing") == "mcp__my_server__do_thing"
        assert MCPToolName.normalize("claude.ai Figma", "get-screenshot") == "mcp__claude_ai_figma__get_screenshot"

    def test_parse_valid(self):
        assert MCPToolName.parse("mcp__my_server__do_thing") == ("my_server", "do_thing")
        assert MCPToolName.parse("mcp__srv__a_b_c") == ("srv", "a_b_c")

    def test_parse_invalid(self):
        assert MCPToolName.parse("not_mcp") is None
        assert MCPToolName.parse("mcp__") is None
        assert MCPToolName.parse("mcp__nodelim") is None

    def test_is_mcp_tool(self):
        assert MCPToolName.is_mcp_tool("mcp__srv__tool") is True
        assert MCPToolName.is_mcp_tool("regular_tool") is False
        assert MCPToolName.is_mcp_tool("mcp__") is False

    def test_roundtrip(self):
        name = MCPToolName.normalize("test-srv", "run-query")
        parsed = MCPToolName.parse(name)
        assert parsed is not None
        assert parsed == ("test_srv", "run_query")


# ---------------------------------------------------------------------------
# MCPServerConfig
# ---------------------------------------------------------------------------

class TestMCPServerConfig:
    def test_defaults(self):
        cfg = MCPServerConfig(name="test")
        assert cfg.transport == "stdio"
        assert cfg.enabled is True
        assert cfg.scope == "user"
        assert cfg.timeout == 30
        assert cfg.args == []
        assert cfg.env == {}

    def test_full_command(self):
        cfg = MCPServerConfig(name="t", command="node", args=["server.js", "--port", "3000"])
        assert cfg.full_command() == ["node", "server.js", "--port", "3000"]

    def test_full_command_no_command(self):
        cfg = MCPServerConfig(name="t")
        assert cfg.full_command() == []


# ---------------------------------------------------------------------------
# MCPConnection
# ---------------------------------------------------------------------------

class TestMCPConnection:
    def test_not_connected_initially(self):
        cfg = MCPServerConfig(name="test", command="echo")
        conn = MCPConnection(cfg)
        assert conn.is_connected() is False
        assert conn._tools == []

    def test_non_stdio_transport_fails(self):
        cfg = MCPServerConfig(name="test", transport="sse", url="http://localhost")
        conn = MCPConnection(cfg)
        assert conn.connect() is False
        assert "not yet implemented" in conn._last_error

    def test_no_command_fails(self):
        cfg = MCPServerConfig(name="test", command=None)
        conn = MCPConnection(cfg)
        assert conn.connect() is False
        assert "No command" in conn._last_error

    def test_call_tool_not_connected(self):
        cfg = MCPServerConfig(name="test", command="echo")
        conn = MCPConnection(cfg)
        result = conn.call_tool("foo", {})
        assert "not connected" in result

    def test_generate_id_increments(self):
        cfg = MCPServerConfig(name="test", command="echo")
        conn = MCPConnection(cfg)
        id1 = conn._generate_id()
        id2 = conn._generate_id()
        assert id2 == id1 + 1

    def test_disconnect_clears_state(self):
        cfg = MCPServerConfig(name="test", command="echo")
        conn = MCPConnection(cfg)
        conn._connected = True
        conn._tools = [{"name": "t"}]
        conn.disconnect()
        assert conn.is_connected() is False
        assert conn._tools == []


# ---------------------------------------------------------------------------
# MCPConfigLoader
# ---------------------------------------------------------------------------

class TestMCPConfigLoader:
    def test_merge_project_overrides_user(self):
        user = [MCPServerConfig(name="srv", command="old")]
        project = [MCPServerConfig(name="srv", command="new", scope="project")]
        merged = MCPConfigLoader.merge_configs(user, project)
        assert len(merged) == 1
        assert merged[0].command == "new"
        assert merged[0].scope == "project"

    def test_merge_combines(self):
        user = [MCPServerConfig(name="a", command="a")]
        project = [MCPServerConfig(name="b", command="b")]
        merged = MCPConfigLoader.merge_configs(user, project)
        assert len(merged) == 2

    def test_expand_env_vars(self):
        import os
        os.environ["_JARVIS_TEST_VAR"] = "hello"
        cfg = MCPServerConfig(
            name="test",
            command="$_JARVIS_TEST_VAR",
            args=["$_JARVIS_TEST_VAR"],
            env={"K": "$_JARVIS_TEST_VAR"},
        )
        expanded = MCPConfigLoader.expand_env_vars(cfg)
        assert expanded.command == "hello"
        assert expanded.args == ["hello"]
        assert expanded.env["K"] == "hello"
        del os.environ["_JARVIS_TEST_VAR"]

    def test_hash_stable(self):
        cfgs = [MCPServerConfig(name="s", command="c")]
        h1 = MCPConfigLoader.hash_config(cfgs)
        h2 = MCPConfigLoader.hash_config(cfgs)
        assert h1 == h2
        assert len(h1) == 16

    def test_hash_excludes_scope(self):
        c1 = [MCPServerConfig(name="s", command="c", scope="user")]
        c2 = [MCPServerConfig(name="s", command="c", scope="project")]
        assert MCPConfigLoader.hash_config(c1) == MCPConfigLoader.hash_config(c2)

    def test_hash_changes_on_content(self):
        c1 = [MCPServerConfig(name="s", command="old")]
        c2 = [MCPServerConfig(name="s", command="new")]
        assert MCPConfigLoader.hash_config(c1) != MCPConfigLoader.hash_config(c2)

    def test_load_file_missing(self):
        configs = MCPConfigLoader._load_file(
            __import__("pathlib").Path("/nonexistent/mcp.json"), scope="user"
        )
        assert configs == []

    def test_load_file_valid(self, tmp_path):
        p = tmp_path / "mcp.json"
        p.write_text(json.dumps({
            "mcpServers": {
                "demo": {"command": "node", "args": ["srv.js"], "env": {"K": "V"}}
            }
        }))
        configs = MCPConfigLoader._load_file(p, scope="project")
        assert len(configs) == 1
        assert configs[0].name == "demo"
        assert configs[0].command == "node"
        assert configs[0].args == ["srv.js"]
        assert configs[0].scope == "project"


# ---------------------------------------------------------------------------
# MCPToolProxy
# ---------------------------------------------------------------------------

class TestMCPToolProxy:
    def test_truncate_short(self):
        assert MCPToolProxy.truncate_description("short") == "short"

    def test_truncate_long(self):
        long_desc = "word " * 600  # ~3000 chars
        result = MCPToolProxy.truncate_description(long_desc, max_chars=100)
        assert len(result) <= 110  # some slack for the ellipsis
        assert result.endswith(" ...")

    def test_truncate_exact_boundary(self):
        desc = "a" * 2048
        assert MCPToolProxy.truncate_description(desc) == desc

    def test_truncate_one_over(self):
        desc = "a" * 2049
        result = MCPToolProxy.truncate_description(desc)
        # No spaces to split on, so we get 2048 chars + " ..."
        assert result.endswith(" ...")
        assert len(result) <= 2048 + 4


# ---------------------------------------------------------------------------
# MCPHealthChecker
# ---------------------------------------------------------------------------

class TestMCPHealthChecker:
    def test_disabled_server(self):
        cfg = MCPServerConfig(name="off", enabled=False)
        result = MCPHealthChecker.check_server(cfg)
        assert result["status"] == "disabled"
        assert result["tools"] == 0

    def test_bad_command(self):
        cfg = MCPServerConfig(name="bad", command="/nonexistent_binary_xyz")
        result = MCPHealthChecker.check_server(cfg)
        assert result["status"] == "error"
        assert result["error"] is not None

    def test_check_all(self):
        configs = [
            MCPServerConfig(name="disabled", enabled=False),
            MCPServerConfig(name="bad", command="/nonexistent_binary_xyz"),
        ]
        results = MCPHealthChecker.check_all(configs)
        assert len(results) == 2
        assert results[0]["status"] == "disabled"
        assert results[1]["status"] == "error"
