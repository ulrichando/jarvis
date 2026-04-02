"""MCP Client — communicates with MCP servers via stdio JSON-RPC."""
import json
import subprocess
import logging
from dataclasses import dataclass, field

log = logging.getLogger("jarvis.mcp")

@dataclass
class MCPTool:
    """A tool provided by an MCP server."""
    name: str
    description: str
    parameters: dict = field(default_factory=dict)
    server_name: str = ""

class MCPClient:
    """Client for a single MCP server."""

    def __init__(self, name: str, command: list[str], env: dict | None = None):
        self.name = name
        self.command = command
        self.env = env or {}
        self._process: subprocess.Popen | None = None
        self._request_id = 0
        self._tools: list[MCPTool] = []

    def start(self) -> bool:
        """Start the MCP server process."""
        try:
            import os
            merged_env = {**os.environ, **self.env}
            self._process = subprocess.Popen(
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=merged_env,
                text=True,
                bufsize=1,
            )
            # Send initialize
            resp = self._rpc("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "jarvis", "version": "1.0.0"},
            })
            if resp and "capabilities" in resp.get("result", {}):
                log.info("MCP server '%s' initialized", self.name)
                # Send initialized notification
                self._notify("notifications/initialized", {})
                return True
            log.warning("MCP server '%s' init failed: %s", self.name, resp)
            return False
        except Exception as e:
            log.error("Failed to start MCP server '%s': %s", self.name, e)
            return False

    def list_tools(self) -> list[MCPTool]:
        """Get available tools from the server."""
        resp = self._rpc("tools/list", {})
        if not resp or "error" in resp:
            return []
        tools = []
        for t in resp.get("result", {}).get("tools", []):
            tools.append(MCPTool(
                name=t["name"],
                description=t.get("description", ""),
                parameters=t.get("inputSchema", {}),
                server_name=self.name,
            ))
        self._tools = tools
        return tools

    def call_tool(self, tool_name: str, arguments: dict, timeout: int = 30) -> str:
        """Call a tool on the server."""
        resp = self._rpc("tools/call", {
            "name": tool_name,
            "arguments": arguments,
        }, timeout=timeout)
        if not resp:
            return f"MCP error: no response from {self.name}"
        if "error" in resp:
            return f"MCP error: {resp['error'].get('message', 'unknown')}"
        result = resp.get("result", {})
        # Extract text content
        content_parts = result.get("content", [])
        texts = []
        for part in content_parts:
            if part.get("type") == "text":
                texts.append(part["text"])
        return "\n".join(texts) if texts else json.dumps(result)

    def stop(self):
        """Shut down the MCP server."""
        if self._process:
            try:
                self._notify("notifications/cancelled", {"requestId": self._request_id})
                self._process.terminate()
                self._process.wait(timeout=5)
            except Exception:
                if self._process:
                    self._process.kill()
            self._process = None

    def _rpc(self, method: str, params: dict, timeout: int = 10) -> dict | None:
        """Send a JSON-RPC request and get response."""
        if not self._process or self._process.poll() is not None:
            return None
        self._request_id += 1
        request = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params,
        }
        try:
            line = json.dumps(request) + "\n"
            self._process.stdin.write(line)
            self._process.stdin.flush()

            # Read response (simple blocking read)
            import select
            ready, _, _ = select.select([self._process.stdout], [], [], timeout)
            if ready:
                resp_line = self._process.stdout.readline()
                if resp_line:
                    return json.loads(resp_line)
            return None
        except Exception as e:
            log.error("MCP RPC error (%s.%s): %s", self.name, method, e)
            return None

    def _notify(self, method: str, params: dict):
        """Send a JSON-RPC notification (no response expected)."""
        if not self._process or self._process.poll() is not None:
            return
        notification = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        try:
            line = json.dumps(notification) + "\n"
            self._process.stdin.write(line)
            self._process.stdin.flush()
        except Exception:
            pass

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None
