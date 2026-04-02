"""MCP and tool management commands — tools, servers, hooks."""
import json
import logging

from brain.commands.registry import command, CommandContext, CommandResult, PermLevel

log = logging.getLogger("jarvis.commands.mcp")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_mcp(brain):
    """Return brain.mcp (MCPManager) or None."""
    if brain and hasattr(brain, 'mcp'):
        return brain.mcp
    return None


def _format_tool(tool: dict, indent: int = 4) -> str:
    """Format a single tool for display."""
    pad = " " * indent
    desc = tool.get("description", "No description")
    if len(desc) > 60:
        desc = desc[:57] + "..."
    return f"{pad}{tool['name']:<30s} {desc}"


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

@command("tools", description="List all tools (built-in + MCP), grouped by source",
         usage="/tools", category="mcp", permission=PermLevel.READ_ONLY)
async def cmd_tools(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available", success=False)

    lines = ["Available Tools", "=" * 50]

    # Built-in tools from agent.tools
    try:
        from brain.agent import tools as agent_tools
        builtin = []
        if hasattr(agent_tools, 'TOOL_REGISTRY'):
            builtin = list(agent_tools.TOOL_REGISTRY.keys())
        elif hasattr(agent_tools, 'get_tools'):
            builtin = [t['name'] if isinstance(t, dict) else t.name for t in agent_tools.get_tools()]

        if builtin:
            lines.append(f"\n  Built-in ({len(builtin)}):")
            lines.append("  " + "-" * 20)
            for name in sorted(builtin):
                lines.append(f"    {name}")
    except ImportError:
        lines.append("\n  Built-in: (module not loaded)")

    # MCP tools grouped by server
    mcp = _get_mcp(brain)
    if mcp:
        servers = mcp.list_servers()
        all_tools = mcp.list_tools()
        if all_tools:
            lines.append(f"\n  MCP Tools ({len(all_tools)}):")
            lines.append("  " + "-" * 20)
            # Group by server
            by_server = {}
            for tool in all_tools:
                server = tool.get("server", "unknown")
                by_server.setdefault(server, []).append(tool)
            for server, tools in sorted(by_server.items()):
                lines.append(f"\n    [{server}] ({len(tools)} tools)")
                for t in tools:
                    lines.append(_format_tool(t, indent=6))
        elif servers:
            lines.append(f"\n  MCP Servers: {len(servers)} connected, 0 tools loaded")
    else:
        lines.append("\n  MCP: not initialized")

    return CommandResult(text="\n".join(lines))


@command("tool-search", aliases=["ts"], description="Search tools by name or description",
         usage="/tool-search <query>", category="mcp", permission=PermLevel.READ_ONLY)
async def cmd_tool_search(ctx: CommandContext) -> CommandResult:
    query = ctx.args.strip().lower()
    if not query:
        return CommandResult(text="Usage: /tool-search <query>", success=False)

    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available", success=False)

    matches = []

    # Search built-in tools
    try:
        from brain.agent import tools as agent_tools
        if hasattr(agent_tools, 'TOOL_REGISTRY'):
            for name, tool in agent_tools.TOOL_REGISTRY.items():
                desc = getattr(tool, 'description', '') or ''
                if query in name.lower() or query in desc.lower():
                    matches.append({"name": name, "description": desc, "source": "built-in"})
    except ImportError:
        pass

    # Search MCP tools
    mcp = _get_mcp(brain)
    if mcp:
        for tool in mcp.list_tools():
            name = tool.get("name", "")
            desc = tool.get("description", "")
            if query in name.lower() or query in desc.lower():
                matches.append({"name": name, "description": desc, "source": tool.get("server", "mcp")})

    if not matches:
        return CommandResult(text=f"No tools matching '{query}'.")

    lines = [f"Tools matching '{query}' ({len(matches)})", "=" * 40]
    for m in matches:
        desc = m['description'][:50] if m['description'] else "No description"
        lines.append(f"  [{m['source']}] {m['name']:<25s} {desc}")
    return CommandResult(text="\n".join(lines))


@command("mcp", description="Show MCP server status",
         usage="/mcp", category="mcp", permission=PermLevel.READ_ONLY)
async def cmd_mcp(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    mcp = _get_mcp(brain)
    if not mcp:
        return CommandResult(text="MCP manager not available", success=False)

    servers = mcp.list_servers()
    all_tools = mcp.list_tools()

    lines = ["MCP Status", "=" * 40]
    lines.append(f"  Connected Servers: {len(servers)}")
    lines.append(f"  Total Tools:       {len(all_tools)}")

    if servers:
        lines.append("\n  Servers:")
        for s in servers:
            name = s if isinstance(s, str) else s.get("name", "unknown")
            tool_count = sum(1 for t in all_tools if t.get("server") == name)
            status = "connected" if not isinstance(s, dict) else s.get("status", "connected")
            lines.append(f"    {name:<20s} {status:<12s} {tool_count} tools")

    return CommandResult(text="\n".join(lines))


@command("mcp-connect", description="Connect to a new MCP server",
         usage="/mcp-connect <name> <command>", category="mcp", permission=PermLevel.FULL)
async def cmd_mcp_connect(ctx: CommandContext) -> CommandResult:
    args = ctx.args.strip()
    if not args:
        return CommandResult(
            text="Usage: /mcp-connect <name> <command>\nExample: /mcp-connect github npx @modelcontextprotocol/server-github",
            success=False,
        )

    parts = args.split(None, 1)
    if len(parts) < 2:
        return CommandResult(text="Provide both a name and a command.", success=False)

    name, cmd = parts[0], parts[1]

    brain = ctx.brain
    mcp = _get_mcp(brain)
    if not mcp:
        return CommandResult(text="MCP manager not available", success=False)

    try:
        await mcp.connect(name=name, command=cmd)
        tools = [t for t in mcp.list_tools() if t.get("server") == name]
        return CommandResult(text=f"Connected to '{name}' ({len(tools)} tools available)")
    except Exception as e:
        return CommandResult(text=f"Failed to connect to '{name}': {e}", success=False)


@command("mcp-disconnect", description="Disconnect from an MCP server",
         usage="/mcp-disconnect <name>", category="mcp", permission=PermLevel.FULL)
async def cmd_mcp_disconnect(ctx: CommandContext) -> CommandResult:
    name = ctx.args.strip()
    if not name:
        return CommandResult(text="Usage: /mcp-disconnect <name>", success=False)

    brain = ctx.brain
    mcp = _get_mcp(brain)
    if not mcp:
        return CommandResult(text="MCP manager not available", success=False)

    try:
        await mcp.disconnect(name)
        return CommandResult(text=f"Disconnected from '{name}'")
    except Exception as e:
        return CommandResult(text=f"Failed to disconnect from '{name}': {e}", success=False)


@command("mcp-tools", description="List tools from a specific MCP server",
         usage="/mcp-tools [server_name]", category="mcp", permission=PermLevel.READ_ONLY)
async def cmd_mcp_tools(ctx: CommandContext) -> CommandResult:
    server_name = ctx.args.strip()

    brain = ctx.brain
    mcp = _get_mcp(brain)
    if not mcp:
        return CommandResult(text="MCP manager not available", success=False)

    all_tools = mcp.list_tools()

    if server_name:
        tools = [t for t in all_tools if t.get("server") == server_name]
        if not tools:
            return CommandResult(text=f"No tools found for server '{server_name}'.", success=False)
        lines = [f"Tools from '{server_name}' ({len(tools)})", "=" * 40]
        for t in tools:
            lines.append(_format_tool(t))
        return CommandResult(text="\n".join(lines))

    # No server specified — list all grouped by server
    by_server = {}
    for t in all_tools:
        by_server.setdefault(t.get("server", "unknown"), []).append(t)

    if not by_server:
        return CommandResult(text="No MCP tools loaded.")

    lines = [f"All MCP Tools ({len(all_tools)})", "=" * 40]
    for server, tools in sorted(by_server.items()):
        lines.append(f"\n  [{server}] ({len(tools)})")
        for t in tools:
            lines.append(_format_tool(t, indent=4))
    return CommandResult(text="\n".join(lines))


@command("serve", aliases=["mcp-server"], description="Start JARVIS as an MCP server (stdio mode)",
         usage="/serve", category="mcp", permission=PermLevel.FULL)
async def cmd_serve(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available", success=False)

    try:
        from brain.mcp.server import start_mcp_server
        # Non-blocking: launch in background
        import asyncio
        asyncio.create_task(start_mcp_server(brain))
        return CommandResult(text="JARVIS MCP server started (stdio mode).\nOther clients can now connect.")
    except ImportError:
        return CommandResult(
            text="MCP server module not available. Create brain/mcp/server.py to enable this feature.",
            success=False,
        )


@command("rpc", description="Directly call an MCP tool with JSON arguments",
         usage="/rpc <tool_name> <json_args>", category="mcp", permission=PermLevel.FULL)
async def cmd_rpc(ctx: CommandContext) -> CommandResult:
    args = ctx.args.strip()
    if not args:
        return CommandResult(text='Usage: /rpc <tool_name> {"key": "value"}', success=False)

    parts = args.split(None, 1)
    tool_name = parts[0]
    json_str = parts[1] if len(parts) > 1 else "{}"

    brain = ctx.brain
    mcp = _get_mcp(brain)
    if not mcp:
        return CommandResult(text="MCP manager not available", success=False)

    try:
        tool_args = json.loads(json_str)
    except json.JSONDecodeError as e:
        return CommandResult(text=f"Invalid JSON: {e}", success=False)

    try:
        result = await mcp.call_tool(tool_name, tool_args)
        if isinstance(result, (dict, list)):
            formatted = json.dumps(result, indent=2, default=str)
        else:
            formatted = str(result)
        return CommandResult(text=f"[{tool_name}] Result:\n{formatted}", data={"result": result})
    except Exception as e:
        return CommandResult(text=f"RPC call to '{tool_name}' failed: {e}", success=False)


@command("hooks", description="List active hooks",
         usage="/hooks", category="mcp", permission=PermLevel.READ_ONLY)
async def cmd_hooks(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available", success=False)

    if not hasattr(brain, 'hooks'):
        return CommandResult(text="Hook system not available.")

    hooks = brain.hooks.list_hooks() if hasattr(brain.hooks, 'list_hooks') else []
    if not hooks:
        return CommandResult(text="No active hooks.")

    lines = [f"Active Hooks ({len(hooks)})", "=" * 40]
    for h in hooks:
        event = h.get("event", "unknown")
        action = h.get("command", h.get("action", "N/A"))
        enabled = "on" if h.get("enabled", True) else "off"
        lines.append(f"  [{enabled}] {event:<25s} -> {action}")
    return CommandResult(text="\n".join(lines))


@command("hook", description="Add or remove a hook",
         usage="/hook <add|remove> <event> <command>", category="mcp", permission=PermLevel.FULL)
async def cmd_hook(ctx: CommandContext) -> CommandResult:
    args = ctx.args.strip()
    if not args:
        return CommandResult(
            text="Usage: /hook add <event> <command>\n       /hook remove <event>\n"
                 "Events: pre_command, post_command, on_error, on_startup, on_shutdown",
            success=False,
        )

    brain = ctx.brain
    if not brain or not hasattr(brain, 'hooks'):
        return CommandResult(text="Hook system not available", success=False)

    parts = args.split(None, 2)
    action = parts[0].lower()

    if action == "add":
        if len(parts) < 3:
            return CommandResult(text="Usage: /hook add <event> <command>", success=False)
        event, cmd = parts[1], parts[2]
        brain.hooks.add_hook(event=event, command=cmd)
        return CommandResult(text=f"Hook added: {event} -> {cmd}")

    elif action == "remove":
        if len(parts) < 2:
            return CommandResult(text="Usage: /hook remove <event>", success=False)
        event = parts[1]
        brain.hooks.remove_hook(event=event)
        return CommandResult(text=f"Hook removed for event: {event}")

    else:
        return CommandResult(text=f"Unknown action: {action}. Use add or remove.", success=False)
