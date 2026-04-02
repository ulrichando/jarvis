"""JARVIS MCP Server — expose JARVIS capabilities via Model Context Protocol.

Run with: jarvis --serve  or  /serve command
Communicates via stdio JSON-RPC 2.0 (MCP protocol).

Exposes tools:
- jarvis_think: Send a query to JARVIS brain
- jarvis_recall: Search JARVIS memory
- jarvis_learn: Teach JARVIS something
- jarvis_bash: Execute shell command with safety
- jarvis_read_file: Read file with path validation
- jarvis_write_file: Write file with checkpointing
- jarvis_search: Search files by pattern
- jarvis_task: Create/manage tasks
- jarvis_agent: Spawn a sub-agent
"""

import json
import sys
import logging
from typing import Any

log = logging.getLogger("jarvis.mcp.server")

TOOLS = [
    {
        "name": "jarvis_think",
        "description": "Send a query to JARVIS AI brain for reasoning and response",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The question or task for JARVIS"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "jarvis_recall",
        "description": "Search JARVIS memory for stored knowledge",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Memory search query"},
                "top_k": {"type": "integer", "description": "Max results", "default": 5},
            },
            "required": ["query"],
        },
    },
    {
        "name": "jarvis_learn",
        "description": "Teach JARVIS a new fact, skill, or concept",
        "inputSchema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "What to learn"},
                "node_type": {"type": "string", "enum": ["fact", "skill", "concept", "entity"], "default": "fact"},
                "tags": {"type": "string", "description": "Comma-separated tags", "default": ""},
            },
            "required": ["content"],
        },
    },
    {
        "name": "jarvis_bash",
        "description": "Execute a shell command on JARVIS host with safety checks",
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to execute"},
                "timeout": {"type": "integer", "description": "Timeout in seconds", "default": 30},
            },
            "required": ["command"],
        },
    },
    {
        "name": "jarvis_read_file",
        "description": "Read a file from JARVIS host filesystem",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path to read"},
                "limit": {"type": "integer", "description": "Max lines", "default": 200},
            },
            "required": ["path"],
        },
    },
    {
        "name": "jarvis_write_file",
        "description": "Write content to a file on JARVIS host",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path"},
                "content": {"type": "string", "description": "File content"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "jarvis_search",
        "description": "Search for files by glob pattern or grep content",
        "inputSchema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Search pattern"},
                "path": {"type": "string", "description": "Directory to search", "default": "."},
                "mode": {"type": "string", "enum": ["glob", "grep"], "default": "glob"},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "jarvis_task",
        "description": "Create or manage JARVIS tasks",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["create", "list", "update", "delete"]},
                "title": {"type": "string", "description": "Task title (for create)"},
                "task_id": {"type": "string", "description": "Task ID (for update/delete)"},
                "status": {"type": "string", "description": "New status (for update)"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "jarvis_agent",
        "description": "Spawn a JARVIS sub-agent for a task",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_type": {"type": "string", "enum": ["scout", "worker", "planner"]},
                "task": {"type": "string", "description": "Task for the agent"},
            },
            "required": ["agent_type", "task"],
        },
    },
]


class MCPServer:
    """JARVIS MCP Server — stdio JSON-RPC 2.0."""

    def __init__(self, brain=None):
        self.brain = brain
        self._running = False

    async def run(self):
        """Main server loop — read stdin, write stdout."""
        import asyncio
        self._running = True
        log.info("JARVIS MCP Server starting on stdio")

        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await asyncio.get_event_loop().connect_read_pipe(lambda: protocol, sys.stdin)

        writer_transport, writer_protocol = await asyncio.get_event_loop().connect_write_pipe(
            asyncio.streams.FlowControlMixin, sys.stdout
        )
        writer = asyncio.StreamWriter(writer_transport, writer_protocol, reader, asyncio.get_event_loop())

        while self._running:
            try:
                line = await reader.readline()
                if not line:
                    break
                request = json.loads(line.decode().strip())
                response = await self._handle_request(request)
                if response:  # notifications don't get responses
                    out = json.dumps(response) + "\n"
                    writer.write(out.encode())
                    await writer.drain()
            except json.JSONDecodeError:
                continue
            except Exception as e:
                log.error("MCP server error: %s", e)

    async def _handle_request(self, request: dict) -> dict | None:
        method = request.get("method", "")
        req_id = request.get("id")
        params = request.get("params", {})

        # Notifications (no id) — no response
        if req_id is None:
            return None

        if method == "initialize":
            return self._response(req_id, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "jarvis", "version": "2.0.0"},
            })

        elif method == "tools/list":
            return self._response(req_id, {"tools": TOOLS})

        elif method == "tools/call":
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})
            result = await self._execute_tool(tool_name, arguments)
            return self._response(req_id, {
                "content": [{"type": "text", "text": result}],
            })

        else:
            return self._error(req_id, -32601, f"Method not found: {method}")

    async def _execute_tool(self, name: str, args: dict) -> str:
        """Execute a JARVIS MCP tool."""
        from brain.agent.tools import execute_tool

        if name == "jarvis_think":
            if self.brain:
                return await self.brain.think(args.get("query", ""))
            return "Brain not available"

        elif name == "jarvis_recall":
            if self.brain:
                results = self.brain.memory.recall(args.get("query", ""), top_k=args.get("top_k", 5))
                return "\n".join(f"- [{r.node_type.value}] {r.content}" for r in results) or "No memories found."
            return "Brain not available"

        elif name == "jarvis_learn":
            if self.brain:
                from brain.memory.lattice.node import NodeType
                type_map = {"fact": NodeType.FACT, "skill": NodeType.SKILL, "concept": NodeType.CONCEPT, "entity": NodeType.ENTITY}
                nt = type_map.get(args.get("node_type", "fact"), NodeType.FACT)
                tags = [t.strip() for t in args.get("tags", "").split(",") if t.strip()]
                node = self.brain.memory.learn(args["content"], node_type=nt, tags=tags)
                return f"Learned: {node.content[:100]} (type={node.node_type.value})"
            return "Brain not available"

        elif name == "jarvis_bash":
            return execute_tool("bash", {"command": args.get("command", ""), "timeout": args.get("timeout", 30)})

        elif name == "jarvis_read_file":
            return execute_tool("read_file", {"path": args.get("path", ""), "limit": args.get("limit", 200)})

        elif name == "jarvis_write_file":
            return execute_tool("write_file", {"path": args.get("path", ""), "content": args.get("content", "")})

        elif name == "jarvis_search":
            return execute_tool("search_files", args)

        elif name == "jarvis_task":
            if not self.brain:
                return "Brain not available"
            action = args.get("action", "list")
            if action == "create":
                t = self.brain.tasks.create(args.get("title", "Untitled"))
                return f"Created task: {t.id} — {t.title}"
            elif action == "list":
                tasks = self.brain.tasks.list_tasks(limit=20)
                return "\n".join(f"[{t.status}] {t.id}: {t.title}" for t in tasks) or "No tasks."
            elif action == "update":
                self.brain.tasks.update_status(args.get("task_id", ""), args.get("status", "done"))
                return "Updated."
            elif action == "delete":
                self.brain.tasks.delete(args.get("task_id", ""))
                return "Deleted."
            return f"Unknown action: {action}"

        elif name == "jarvis_agent":
            if self.brain:
                from brain.agent.loop import _run_sub_agent
                result = await _run_sub_agent(self.brain.reasoner, args.get("agent_type", "scout"), args.get("task", ""))
                return result
            return "Brain not available"

        return f"Unknown tool: {name}"

    def _response(self, req_id, result):
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    def _error(self, req_id, code, message):
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}

    def stop(self):
        self._running = False
