"""MCP and tool management commands — tools, servers, hooks."""
import json
import logging

from src.commands_brain.registry import command, CommandContext, CommandResult, PermLevel

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

@command("tools", description="List all tools (built-in + MCP) with categories and flags",
         usage="/tools [category]", category="mcp", permission=PermLevel.READ_ONLY)
async def cmd_tools(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available", success=False)

    filter_category = ctx.args.strip().lower() or None
    lines = ["Available Tools", "=" * 50]

    # Built-in tools with rich metadata from tool_registry
    try:
        from src.agent.tool_registry import TOOL_REGISTRY, get_deferred_tools
        registry = TOOL_REGISTRY

        # Group by category
        by_category: dict[str, list] = {}
        for name, meta in sorted(registry.items()):
            by_category.setdefault(meta.category, []).append(meta)

        deferred_names = {m.name for m in get_deferred_tools()}

        for cat, metas in sorted(by_category.items()):
            if filter_category and cat != filter_category:
                continue
            lines.append(f"\n  [{cat.upper()}] ({len(metas)} tools)")
            lines.append("  " + "-" * 30)
            for meta in metas:
                flags = []
                if meta.is_read_only:
                    flags.append("RO")
                if meta.is_destructive:
                    flags.append("DESTRUCTIVE")
                if meta.name in deferred_names:
                    flags.append("deferred")
                flag_str = f" ({', '.join(flags)})" if flags else ""
                desc = meta.description[:45] if meta.description else ""
                lines.append(f"    {meta.name:<22s}{flag_str:<20s} {desc}")

    except ImportError:
        # Fallback to basic listing
        try:
            from src.agent import tools as agent_tools
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
        from src.agent import tools as agent_tools
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


@command("mcp", description="MCP server management: list, reconnect, health",
         usage="/mcp [list|reconnect <name>|health]", category="mcp", permission=PermLevel.READ_ONLY)
async def cmd_mcp(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    mcp = _get_mcp(brain)
    if not mcp:
        return CommandResult(text="MCP manager not available", success=False)

    args = ctx.args.strip()
    parts = args.split(None, 1)
    action = parts[0].lower() if parts else "list"
    rest = parts[1].strip() if len(parts) > 1 else ""

    # ── /mcp reconnect <name> ──
    if action == "reconnect":
        if not rest:
            return CommandResult(text="Usage: /mcp reconnect <server_name>", success=False)
        try:
            if hasattr(mcp, 'disconnect'):
                await mcp.disconnect(rest)
            if hasattr(mcp, 'reconnect'):
                await mcp.reconnect(rest)
            elif hasattr(mcp, 'connect'):
                # Re-read config and reconnect
                from src.mcp.enhanced_client import MCPConfigLoader
                configs = MCPConfigLoader.load_config()
                target = next((c for c in configs if c.name == rest), None)
                if target:
                    await mcp.connect(name=rest, command=target.command)
                else:
                    return CommandResult(text=f"Server '{rest}' not found in config.", success=False)
            tools = [t for t in mcp.list_tools() if t.get("server") == rest]
            return CommandResult(text=f"Reconnected to '{rest}' ({len(tools)} tools)")
        except Exception as e:
            return CommandResult(text=f"Reconnect failed for '{rest}': {e}", success=False)

    # ── /mcp health ──
    if action == "health":
        try:
            from src.mcp.enhanced_client import MCPConfigLoader, MCPHealthChecker
            configs = MCPConfigLoader.load_config()
            if not configs:
                return CommandResult(text="No MCP servers configured.")
            results = MCPHealthChecker.check_all(configs)
            lines = ["MCP Health Check", "=" * 50]
            for r in results:
                status_icon = {"ok": "+", "disabled": "~", "error": "!"}
                icon = status_icon.get(r["status"], "?")
                err = f"  ({r['error']})" if r.get("error") else ""
                lines.append(f"  [{icon}] {r['name']:<20s} {r['status']:<10s} {r['tools']} tools{err}")
            return CommandResult(text="\n".join(lines))
        except ImportError:
            return CommandResult(text="Enhanced MCP client not available for health checks.", success=False)

    # ── /mcp list (default) ──
    servers = mcp.list_servers()
    all_tools = mcp.list_tools()

    lines = ["MCP Status", "=" * 40]
    lines.append(f"  Connected Servers: {len(servers)}")
    lines.append(f"  Total Tools:       {len(all_tools)}")

    if servers:
        lines.append(f"\n  {'Server':<20s} {'Status':<14s} {'Tools':>5s}")
        lines.append("  " + "-" * 42)
        for s in servers:
            name = s if isinstance(s, str) else s.get("name", "unknown")
            tool_count = sum(1 for t in all_tools if t.get("server") == name)
            status = "connected" if not isinstance(s, dict) else s.get("status", "connected")
            lines.append(f"  {name:<20s} {status:<14s} {tool_count:>5d}")

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
        from src.mcp.server import start_mcp_server
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


@command("hooks", description="List active hooks with counts per event and matchers",
         usage="/hooks [event]", category="mcp", permission=PermLevel.READ_ONLY)
async def cmd_hooks(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    if not brain or not hasattr(brain, 'hooks'):
        return CommandResult(text="Hook system not available.", success=False)

    from src.hooks import HOOK_EVENTS

    filter_event = ctx.args.strip() or None
    hooks = brain.hooks.list_hooks()
    summary = brain.hooks.summary()

    lines = ["╭─ Hooks ─────────────────────────────────────────────╮"]
    lines.append(f"│  {summary['total']} hook(s) configured" + " " * (39 - len(str(summary['total']))) + "│")

    # Show hook count per event in summary bar
    if hooks:
        by_event_counts: dict[str, int] = {}
        for h in hooks:
            by_event_counts[h["event"]] = by_event_counts.get(h["event"], 0) + 1
        count_parts = [f"{e}: {c}" for e, c in sorted(by_event_counts.items())]
        count_line = "  ".join(count_parts)
        if len(count_line) <= 48:
            lines.append(f"│  {count_line:<50s}│")
        else:
            lines.append(f"│  {count_line[:48]:<50s}│")

    lines.append("├────────────────────────────────────────────────────┤")

    if not hooks:
        lines.append("│  No hooks configured.                              │")
        lines.append("│                                                    │")
        lines.append("│  Add one with:                                     │")
        lines.append("│    /hook add PreToolUse 'scripts/check.sh'         │")
        lines.append("│                                                    │")
        lines.append("│  Or create ~/.jarvis/hooks.yaml:                   │")
        lines.append("│    hooks:                                          │")
        lines.append("│      PreToolUse:                                   │")
        lines.append("│        - matcher: bash                             │")
        lines.append("│          type: command                             │")
        lines.append("│          command: scripts/audit.sh                 │")
    else:
        # Group by event
        by_event: dict[str, list] = {}
        for h in hooks:
            by_event.setdefault(h["event"], []).append(h)

        for event in HOOK_EVENTS:
            if event not in by_event:
                continue
            if filter_event and event.lower() != filter_event.lower():
                continue
            event_hooks = by_event[event]
            lines.append(f"│  {event:<20s}  ({len(event_hooks)} hook{'s' if len(event_hooks) != 1 else ''}){'':>15s}│")
            for h in event_hooks:
                icon = "●" if h["enabled"] else "○"
                htype = h["type"]
                cmd = h["command"]
                if len(cmd) > 38:
                    cmd = cmd[:35] + "..."
                matcher_str = f" [{h['matcher']}]" if h["matcher"] else " [*]"
                if_str = f" if={h['if']}" if h.get("if") else ""
                src = h.get("source", "")
                src_tag = f" ({src})" if src and src != "config" else ""

                detail = f"{icon} {htype}: {cmd}{matcher_str}{if_str}{src_tag}"
                # Pad to box width
                pad = 50 - len(detail)
                if pad < 0:
                    detail = detail[:47] + "..."
                    pad = 0
                lines.append(f"│  {detail}{' ' * pad}│")
            lines.append("│                                                    │")

    lines.append("├────────────────────────────────────────────────────┤")
    events_str = ", ".join(HOOK_EVENTS)
    lines.append(f"│  Events: {events_str[:41]:<41s} │")
    if len(events_str) > 41:
        lines.append(f"│          {events_str[41:82]:<41s} │")
    lines.append("╰────────────────────────────────────────────────────╯")

    return CommandResult(text="\n".join(lines))


@command("hook", description="Add, remove, or save hooks",
         usage="/hook add <event> <command> [--matcher <pattern>]\n"
               "       /hook remove <event> [command]\n"
               "       /hook save [path]",
         category="mcp", permission=PermLevel.FULL)
async def cmd_hook(ctx: CommandContext) -> CommandResult:
    from src.hooks import HOOK_EVENTS

    args = ctx.args.strip()
    if not args:
        events_list = ", ".join(HOOK_EVENTS)
        return CommandResult(
            text=f"Usage:\n"
                 f"  /hook add <event> <command> [--matcher <pattern>]\n"
                 f"  /hook remove <event> [command]\n"
                 f"  /hook save [path]\n\n"
                 f"Events: {events_list}\n\n"
                 f"Examples:\n"
                 f"  /hook add PreToolUse 'scripts/audit.sh' --matcher bash\n"
                 f"  /hook add PostToolUse 'python -m py_compile' --matcher 'edit_file|write_file'\n"
                 f"  /hook add Stop 'scripts/final-check.sh'\n"
                 f"  /hook remove PreToolUse\n"
                 f"  /hook save",
            success=False,
        )

    brain = ctx.brain
    if not brain or not hasattr(brain, 'hooks'):
        return CommandResult(text="Hook system not available", success=False)

    parts = args.split(None, 2)
    action = parts[0].lower()

    if action == "add":
        if len(parts) < 3:
            return CommandResult(text="Usage: /hook add <event> <command> [--matcher <pattern>]", success=False)
        event = parts[1]
        rest = parts[2]

        # Parse --matcher flag
        matcher = ""
        if "--matcher" in rest:
            idx = rest.index("--matcher")
            cmd_part = rest[:idx].strip().strip("'\"")
            matcher_part = rest[idx + len("--matcher"):].strip().strip("'\"")
            matcher = matcher_part.split()[0] if matcher_part else ""
        else:
            cmd_part = rest.strip().strip("'\"")

        ok = brain.hooks.add_hook(event=event, command=cmd_part, matcher=matcher)
        if not ok:
            return CommandResult(
                text=f"Unknown event: {event}\nValid events: {', '.join(HOOK_EVENTS)}",
                success=False,
            )
        matcher_info = f" (matcher: {matcher})" if matcher else ""
        return CommandResult(text=f"Hook added: {event} → {cmd_part}{matcher_info}")

    elif action == "remove":
        if len(parts) < 2:
            return CommandResult(text="Usage: /hook remove <event> [command]", success=False)
        event = parts[1]
        cmd = parts[2] if len(parts) > 2 else ""
        ok = brain.hooks.remove_hook(event=event, command=cmd)
        if ok:
            return CommandResult(text=f"Hook(s) removed for: {event}")
        else:
            return CommandResult(text=f"No hooks found for event: {event}", success=False)

    elif action == "save":
        path = parts[1] if len(parts) > 1 else None
        from pathlib import Path
        brain.hooks.save_to_yaml(Path(path) if path else None)
        target = path or ".jarvis/hooks.yaml"
        return CommandResult(text=f"Hooks saved to {target}")

    else:
        return CommandResult(text=f"Unknown action: {action}. Use add, remove, or save.", success=False)
