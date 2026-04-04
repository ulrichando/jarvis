"""MCP Manager — discovers and manages MCP server connections."""
import json
import logging
from pathlib import Path
from src.config import JARVIS_HOME
from src.mcp.client import MCPClient, MCPTool

log = logging.getLogger("jarvis.mcp")

class MCPManager:
    """Manages multiple MCP server connections."""

    def __init__(self):
        self._clients: dict[str, MCPClient] = {}
        self._tools: dict[str, MCPTool] = {}  # tool_name -> MCPTool

    def load_config(self):
        """Load MCP server configs from settings files.

        Reads from:
        - ~/.jarvis/mcp.json
        - .jarvis/mcp.json (project-level)
        """
        for config_path in [
            JARVIS_HOME / "mcp.json",
            Path.cwd() / ".jarvis" / "mcp.json",
        ]:
            if config_path.exists():
                try:
                    data = json.loads(config_path.read_text())
                    servers = data.get("mcpServers", data.get("servers", {}))
                    for name, cfg in servers.items():
                        command = cfg.get("command", [])
                        if isinstance(command, str):
                            command = command.split()
                        args = cfg.get("args", [])
                        env = cfg.get("env", {})
                        full_command = [command] if isinstance(command, str) else command
                        if args:
                            full_command.extend(args)
                        self._clients[name] = MCPClient(
                            name=name,
                            command=full_command,
                            env=env,
                        )
                    log.info("Loaded %d MCP servers from %s", len(servers), config_path)
                except Exception as e:
                    log.error("Failed to load MCP config %s: %s", config_path, e)

    def start_all(self):
        """Start all configured MCP servers and discover tools."""
        for name, client in list(self._clients.items()):
            if client.start():
                tools = client.list_tools()
                for tool in tools:
                    qualified_name = f"mcp_{name}_{tool.name}"
                    tool.server_name = name
                    self._tools[qualified_name] = tool
                log.info("MCP '%s': %d tools available", name, len(tools))
            else:
                log.warning("MCP '%s' failed to start, removing", name)
                del self._clients[name]

    def start_server(self, name: str) -> bool:
        """Start a specific MCP server."""
        client = self._clients.get(name)
        if not client:
            return False
        if client.start():
            tools = client.list_tools()
            for tool in tools:
                qualified_name = f"mcp_{name}_{tool.name}"
                tool.server_name = name
                self._tools[qualified_name] = tool
            return True
        return False

    def call_tool(self, qualified_name: str, arguments: dict, timeout: int = 30) -> str:
        """Call an MCP tool by its qualified name."""
        tool = self._tools.get(qualified_name)
        if not tool:
            return f"Unknown MCP tool: {qualified_name}"
        client = self._clients.get(tool.server_name)
        if not client or not client.is_running:
            return f"MCP server '{tool.server_name}' is not running"
        return client.call_tool(tool.name, arguments, timeout)

    def get_tool_schemas(self) -> list[dict]:
        """Get OpenAI-format tool schemas for all MCP tools."""
        schemas = []
        for qname, tool in self._tools.items():
            schemas.append({
                "type": "function",
                "function": {
                    "name": qname,
                    "description": f"[MCP:{tool.server_name}] {tool.description}",
                    "parameters": tool.parameters or {"type": "object", "properties": {}},
                },
            })
        return schemas

    def list_tools(self) -> list[dict]:
        """List all available MCP tools."""
        return [
            {"name": qname, "server": t.server_name, "description": t.description}
            for qname, t in self._tools.items()
        ]

    def list_servers(self) -> list[dict]:
        """List all configured MCP servers."""
        return [
            {"name": name, "running": client.is_running, "tools": len([t for t in self._tools.values() if t.server_name == name])}
            for name, client in self._clients.items()
        ]

    def stop_all(self):
        """Stop all MCP servers."""
        for client in self._clients.values():
            client.stop()
        self._tools.clear()

    def stop_server(self, name: str):
        """Stop a specific MCP server."""
        client = self._clients.get(name)
        if client:
            client.stop()
            self._tools = {k: v for k, v in self._tools.items() if v.server_name != name}
