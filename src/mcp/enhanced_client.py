"""Enhanced MCP Client — inspired by Claude Code's MCP implementation.

Provides robust tool name normalization, multi-transport config,
JSON-RPC 2.0 stdio communication, config loading with merging and
change detection, tool schema conversion, and health checking.

Can be used alongside or as a drop-in replacement for the existing
MCPClient/MCPManager.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import select
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("jarvis.mcp.enhanced")

# ---------------------------------------------------------------------------
# 1. MCPToolName — tool name normalization
# ---------------------------------------------------------------------------

class MCPToolName:
    """Normalize and parse MCP tool names.

    Convention: ``mcp__<server>__<tool>`` where both server and tool are
    sanitized to contain only ``[a-z0-9_]``.  The double-underscore
    delimiter is never produced by ``sanitize_name`` so parsing is
    unambiguous.
    """

    _PREFIX = "mcp__"
    _DELIM = "__"

    @staticmethod
    def sanitize_name(name: str) -> str:
        """Replace spaces, dots, hyphens and other non-alphanumeric chars
        with underscores, collapse runs, strip edges, lowercase.

        For claude.ai-style server names (e.g. ``claude.ai Figma``), we also
        collapse consecutive underscores and strip leading/trailing ones so
        they don't interfere with the ``__`` delimiter.
        """
        sanitized = re.sub(r"[^a-zA-Z0-9_]", "_", name)
        sanitized = re.sub(r"_+", "_", sanitized)
        sanitized = sanitized.strip("_")
        return sanitized.lower()

    @classmethod
    def normalize(cls, server_name: str, tool_name: str) -> str:
        """Build the canonical qualified tool name.

        >>> MCPToolName.normalize("my-server", "do_thing")
        'mcp__my_server__do_thing'
        """
        srv = cls.sanitize_name(server_name)
        tool = cls.sanitize_name(tool_name)
        return f"{cls._PREFIX}{srv}{cls._DELIM}{tool}"

    @classmethod
    def parse(cls, normalized_name: str) -> tuple[str, str] | None:
        """Extract ``(server, tool)`` from a normalized MCP tool name.

        Returns ``None`` if the string doesn't match the expected format.
        """
        if not normalized_name.startswith(cls._PREFIX):
            return None
        rest = normalized_name[len(cls._PREFIX):]
        # Split on the first __ delimiter (server names cannot contain __)
        parts = rest.split(cls._DELIM, 1)
        if len(parts) != 2 or not parts[0] or not parts[1]:
            return None
        return (parts[0], parts[1])

    @classmethod
    def is_mcp_tool(cls, name: str) -> bool:
        """Check whether *name* looks like an MCP-qualified tool name."""
        return name.startswith(cls._PREFIX) and cls.parse(name) is not None


# ---------------------------------------------------------------------------
# 2. MCPServerConfig
# ---------------------------------------------------------------------------

@dataclass
class MCPServerConfig:
    """Configuration for a single MCP server."""

    name: str
    # stdio transport
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    # HTTP/SSE transport
    url: str | None = None
    transport: str = "stdio"  # "stdio", "sse", "http"
    enabled: bool = True
    scope: str = "user"  # "user", "project"
    timeout: int = 30

    def full_command(self) -> list[str]:
        """Return the full command list (command + args) for stdio transport."""
        if not self.command:
            return []
        parts = self.command.split() if isinstance(self.command, str) else [self.command]
        return parts + list(self.args)


# ---------------------------------------------------------------------------
# 3. MCPConnection — stdio JSON-RPC 2.0 transport
# ---------------------------------------------------------------------------

class MCPConnection:
    """Manages a running MCP server process and JSON-RPC communication.

    Currently implements the **stdio** transport (the most common one).
    HTTP/SSE transports can be added later by subclassing or extending
    ``_send_jsonrpc`` / ``_recv_jsonrpc``.
    """

    MCP_PROTOCOL_VERSION = "2024-11-05"

    def __init__(self, config: MCPServerConfig):
        self.config = config
        self._process: subprocess.Popen | None = None
        self._connected: bool = False
        self._tools: list[dict] = []
        self._last_error: str = ""
        self._request_id: int = 0
        self._lock = threading.Lock()
        self._server_capabilities: dict = {}

    # -- lifecycle -----------------------------------------------------------

    def connect(self) -> bool:
        """Start the MCP server process and perform the protocol handshake.

        Returns ``True`` on success.
        """
        if self.config.transport != "stdio":
            self._last_error = f"Transport '{self.config.transport}' not yet implemented; only 'stdio' is supported"
            log.warning(self._last_error)
            return False

        cmd = self.config.full_command()
        if not cmd:
            self._last_error = "No command configured for stdio transport"
            return False

        try:
            merged_env = {**os.environ, **self.config.env}
            self._process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=merged_env,
                text=True,
                bufsize=1,
            )
        except Exception as exc:
            self._last_error = f"Failed to spawn process: {exc}"
            log.error("MCP '%s': %s", self.config.name, self._last_error)
            return False

        # JSON-RPC initialize handshake
        resp = self._send_jsonrpc("initialize", {
            "protocolVersion": self.MCP_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "jarvis", "version": "2.0.0"},
        })

        if not resp or "error" in resp:
            err_detail = resp.get("error", {}).get("message", "no response") if resp else "no response"
            self._last_error = f"Initialize failed: {err_detail}"
            log.warning("MCP '%s': %s", self.config.name, self._last_error)
            self.disconnect()
            return False

        self._server_capabilities = resp.get("result", {}).get("capabilities", {})

        # Send the initialized notification (required by MCP spec)
        self._send_notification("notifications/initialized", {})

        self._connected = True
        log.info("MCP '%s' connected (capabilities: %s)",
                 self.config.name, list(self._server_capabilities.keys()))
        return True

    def disconnect(self):
        """Gracefully stop the server process."""
        self._connected = False
        self._tools = []
        if self._process:
            try:
                self._process.terminate()
                self._process.wait(timeout=5)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
            self._process = None

    def is_connected(self) -> bool:
        """Check whether the server process is still alive and initialized."""
        if not self._connected or not self._process:
            return False
        if self._process.poll() is not None:
            self._connected = False
            self._last_error = f"Process exited with code {self._process.returncode}"
            return False
        return True

    # -- tool operations -----------------------------------------------------

    def list_tools(self) -> list[dict]:
        """Send ``tools/list`` and cache the result.

        Each tool dict has keys: ``name``, ``description``, ``inputSchema``.
        """
        if not self.is_connected():
            return []

        resp = self._send_jsonrpc("tools/list", {})
        if not resp or "error" in resp:
            self._last_error = f"tools/list failed: {resp}"
            return []

        raw_tools = resp.get("result", {}).get("tools", [])
        self._tools = raw_tools
        return self._tools

    def call_tool(self, tool_name: str, args: dict) -> str:
        """Send ``tools/call`` and return the text result.

        Non-text content blocks are JSON-serialized.  If the server
        reports ``isError: true`` the result is prefixed with ``[ERROR]``.
        """
        if not self.is_connected():
            return f"MCP server '{self.config.name}' is not connected"

        resp = self._send_jsonrpc("tools/call", {
            "name": tool_name,
            "arguments": args,
        }, timeout=self.config.timeout)

        if not resp:
            return f"MCP error: no response from '{self.config.name}'"
        if "error" in resp:
            return f"MCP error: {resp['error'].get('message', 'unknown error')}"

        result = resp.get("result", {})
        is_error = result.get("isError", False)
        content_parts = result.get("content", [])

        texts: list[str] = []
        for part in content_parts:
            ptype = part.get("type", "text")
            if ptype == "text":
                texts.append(part.get("text", ""))
            elif ptype == "image":
                texts.append(f"[image: {part.get('mimeType', 'unknown')}]")
            elif ptype == "resource":
                res = part.get("resource", {})
                texts.append(f"[resource: {res.get('uri', 'unknown')}]")
            else:
                texts.append(json.dumps(part))

        output = "\n".join(texts) if texts else json.dumps(result)
        if is_error:
            output = f"[ERROR] {output}"
        return output

    # -- JSON-RPC transport --------------------------------------------------

    def _generate_id(self) -> int:
        """Return the next request ID (thread-safe)."""
        with self._lock:
            self._request_id += 1
            return self._request_id

    def _send_jsonrpc(self, method: str, params: dict | None = None,
                      timeout: int | None = None) -> dict | None:
        """Send a JSON-RPC 2.0 request and wait for the matching response.

        Skips notification messages from the server while waiting.
        """
        if not self._process or self._process.poll() is not None:
            return None

        req_id = self._generate_id()
        request: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
        }
        if params is not None:
            request["params"] = params

        effective_timeout = timeout if timeout is not None else self.config.timeout

        try:
            line = json.dumps(request) + "\n"
            self._process.stdin.write(line)  # type: ignore[union-attr]
            self._process.stdin.flush()  # type: ignore[union-attr]
            return self._recv_jsonrpc(req_id, effective_timeout)
        except Exception as exc:
            self._last_error = f"RPC error ({method}): {exc}"
            log.error("MCP '%s': %s", self.config.name, self._last_error)
            return None

    def _recv_jsonrpc(self, expected_id: int, timeout: int = 10) -> dict | None:
        """Read lines until we get the response matching *expected_id*.

        Server-initiated notifications (no ``id``) are logged and skipped.
        """
        if not self._process or not self._process.stdout:
            return None

        import time
        deadline = time.monotonic() + timeout

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._last_error = f"Timeout waiting for response id={expected_id}"
                return None

            ready, _, _ = select.select([self._process.stdout], [], [], min(remaining, 1.0))
            if not ready:
                continue

            line = self._process.stdout.readline()
            if not line:
                self._last_error = "Server closed stdout"
                return None

            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                log.debug("MCP '%s': non-JSON line: %s", self.config.name, line.strip())
                continue

            # Skip notifications (no id field)
            if "id" not in msg:
                log.debug("MCP '%s' notification: %s", self.config.name, msg.get("method", "?"))
                continue

            if msg.get("id") == expected_id:
                return msg

            # Mismatched id — probably a late response; log and keep waiting
            log.debug("MCP '%s': got id=%s, expected %s", self.config.name, msg.get("id"), expected_id)

    def _send_notification(self, method: str, params: dict):
        """Send a JSON-RPC 2.0 notification (no ``id``, no response)."""
        if not self._process or self._process.poll() is not None:
            return
        notification: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": method,
        }
        if params:
            notification["params"] = params
        try:
            self._process.stdin.write(json.dumps(notification) + "\n")  # type: ignore[union-attr]
            self._process.stdin.flush()  # type: ignore[union-attr]
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 4. MCPConfigLoader
# ---------------------------------------------------------------------------

class MCPConfigLoader:
    """Load, merge, and hash MCP server configurations."""

    DEFAULT_USER_PATH = Path.home() / ".jarvis" / "mcp.json"
    DEFAULT_PROJECT_PATH = Path.cwd() / ".jarvis" / "mcp.json"

    @classmethod
    def load_config(cls, path: str | None = None) -> list[MCPServerConfig]:
        """Load configs from the given path, or merge user + project defaults."""
        if path:
            return cls._load_file(Path(path), scope="user")

        user = cls._load_file(cls.DEFAULT_USER_PATH, scope="user")
        project = cls._load_file(cls.DEFAULT_PROJECT_PATH, scope="project")
        return cls.merge_configs(user, project)

    @classmethod
    def merge_configs(cls, user: list[MCPServerConfig],
                      project: list[MCPServerConfig]) -> list[MCPServerConfig]:
        """Merge user and project configs; project entries override user by name."""
        by_name: dict[str, MCPServerConfig] = {}
        for cfg in user:
            by_name[cfg.name] = cfg
        for cfg in project:
            by_name[cfg.name] = cfg  # project wins
        return list(by_name.values())

    @classmethod
    def expand_env_vars(cls, config: MCPServerConfig) -> MCPServerConfig:
        """Expand ``$VAR`` and ``${VAR}`` in command, args, and env values.

        Returns a *new* config; the original is not mutated.
        """
        def _expand(s: str) -> str:
            return os.path.expandvars(s)

        return MCPServerConfig(
            name=config.name,
            command=_expand(config.command) if config.command else None,
            args=[_expand(a) for a in config.args],
            env={k: _expand(v) for k, v in config.env.items()},
            url=_expand(config.url) if config.url else None,
            transport=config.transport,
            enabled=config.enabled,
            scope=config.scope,
            timeout=config.timeout,
        )

    @classmethod
    def hash_config(cls, configs: list[MCPServerConfig]) -> str:
        """Return a stable SHA-256 hex digest (first 16 chars) for change detection.

        Excludes ``scope`` so moving a server between user/project configs
        doesn't trigger a reconnect.
        """
        items = []
        for cfg in sorted(configs, key=lambda c: c.name):
            items.append({
                "name": cfg.name,
                "command": cfg.command,
                "args": cfg.args,
                "env": cfg.env,
                "url": cfg.url,
                "transport": cfg.transport,
                "enabled": cfg.enabled,
                "timeout": cfg.timeout,
            })
        blob = json.dumps(items, sort_keys=True).encode()
        return hashlib.sha256(blob).hexdigest()[:16]

    # -- internal ------------------------------------------------------------

    @classmethod
    def _load_file(cls, path: Path, scope: str) -> list[MCPServerConfig]:
        """Parse a single mcp.json file into a list of configs."""
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text())
        except Exception as exc:
            log.error("Failed to read MCP config %s: %s", path, exc)
            return []

        # Support both { "mcpServers": {...} } and { "servers": {...} } shapes
        servers = data.get("mcpServers", data.get("servers", {}))
        configs: list[MCPServerConfig] = []

        for name, raw in servers.items():
            transport = raw.get("type", raw.get("transport", "stdio"))
            cfg = MCPServerConfig(
                name=name,
                command=raw.get("command"),
                args=raw.get("args", []),
                env=raw.get("env", {}),
                url=raw.get("url"),
                transport=transport,
                enabled=raw.get("enabled", True),
                scope=scope,
                timeout=raw.get("timeout", 30),
            )
            configs.append(cfg)

        return configs


# ---------------------------------------------------------------------------
# 5. MCPToolProxy — convert MCP schemas to OpenAI function calling format
# ---------------------------------------------------------------------------

class MCPToolProxy:
    """Convert MCP tool definitions into OpenAI-compatible function schemas."""

    @staticmethod
    def build_tool_schemas(connection: MCPConnection) -> list[dict]:
        """Fetch tools from *connection* and return OpenAI function-calling schemas.

        Each schema uses the normalized ``mcp__<server>__<tool>`` name so the
        agent loop can route calls back through ``MCPConnection.call_tool``.
        """
        raw_tools = connection.list_tools()
        if not raw_tools:
            return []

        schemas: list[dict] = []
        server_name = connection.config.name

        for tool in raw_tools:
            tool_name = tool.get("name", "")
            qualified = MCPToolName.normalize(server_name, tool_name)
            description = tool.get("description", "")
            description = MCPToolProxy.truncate_description(
                f"[MCP:{server_name}] {description}"
            )

            input_schema = tool.get("inputSchema", {"type": "object", "properties": {}})
            # Ensure the schema is a valid JSON-Schema object
            if not isinstance(input_schema, dict):
                input_schema = {"type": "object", "properties": {}}
            if "type" not in input_schema:
                input_schema["type"] = "object"

            schemas.append({
                "type": "function",
                "function": {
                    "name": qualified,
                    "description": description,
                    "parameters": input_schema,
                },
            })

        return schemas

    @staticmethod
    def truncate_description(desc: str, max_chars: int = 2048) -> str:
        """Cap long descriptions (common with OpenAPI-generated MCP servers).

        Truncates at a word boundary and appends an ellipsis indicator.
        """
        if len(desc) <= max_chars:
            return desc
        # Cut at last space before the limit to avoid mid-word truncation
        cut = desc[:max_chars].rsplit(" ", 1)[0]
        return cut + " ..."


# ---------------------------------------------------------------------------
# 6. MCPHealthChecker
# ---------------------------------------------------------------------------

class MCPHealthChecker:
    """Probe MCP servers to verify they are reachable and functional."""

    @staticmethod
    def check_server(config: MCPServerConfig) -> dict:
        """Connect to a single server, list its tools, and disconnect.

        Returns ``{"name": ..., "status": "ok"|"error"|"disabled",
        "tools": int, "error": str|None}``.
        """
        result: dict[str, Any] = {
            "name": config.name,
            "status": "error",
            "tools": 0,
            "error": None,
        }

        if not config.enabled:
            result["status"] = "disabled"
            return result

        expanded = MCPConfigLoader.expand_env_vars(config)
        conn = MCPConnection(expanded)
        try:
            if not conn.connect():
                result["error"] = conn._last_error
                return result

            tools = conn.list_tools()
            result["status"] = "ok"
            result["tools"] = len(tools)
        except Exception as exc:
            result["error"] = str(exc)
        finally:
            conn.disconnect()

        return result

    @classmethod
    def check_all(cls, configs: list[MCPServerConfig]) -> list[dict]:
        """Health-check every server in *configs* sequentially.

        For large server lists, consider running in a thread pool.
        """
        return [cls.check_server(cfg) for cfg in configs]
