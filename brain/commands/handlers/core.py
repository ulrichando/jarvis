"""Core flow commands -- help, status, mode, config."""
from brain.commands.registry import command, CommandContext, CommandResult, PermLevel


@command("help", aliases=["h", "?"], description="Show command reference",
         usage="/help [--all] [command]", category="core", permission=PermLevel.READ_ONLY)
async def cmd_help(ctx: CommandContext) -> CommandResult:
    args = ctx.args.strip()
    if args and not args.startswith("-"):
        # Help for specific command
        from brain.commands.registry import registry
        return CommandResult(text=registry.get_help(args))

    from brain.commands.registry import registry, CATEGORIES
    include_hidden = "--all" in args
    lines = ["JARVIS Commands", "=" * 50]
    for cat_slug, cat_name in CATEGORIES:
        cmds = registry.list_commands(category=cat_slug, include_hidden=include_hidden)
        if not cmds:
            continue
        lines.append(f"\n  {cat_name}")
        lines.append("  " + "-" * len(cat_name))
        for cmd in cmds:
            aliases_str = f" ({', '.join('/' + a for a in cmd.aliases)})" if cmd.aliases else ""
            lines.append(f"  /{cmd.name:<20s} {cmd.description}{aliases_str}")

    if include_hidden:
        hidden = registry.list_commands(include_hidden=True)
        hidden = [c for c in hidden if c.hidden]
        if hidden:
            lines.append(f"\n  Debug/Hidden")
            lines.append("  " + "-" * 12)
            for cmd in hidden:
                lines.append(f"  /{cmd.name:<20s} {cmd.description}")

    lines.append(f"\n  {registry.visible_count} commands available. Use /help <command> for details.")
    return CommandResult(text="\n".join(lines))


@command("status", aliases=["stat"], description="Show model, mode, session, MCP status",
         usage="/status", category="core", permission=PermLevel.READ_ONLY)
async def cmd_status(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    lines = ["JARVIS Status", "=" * 40]
    if brain:
        lines.append(f"  Mode:        {brain.mode}")
        lines.append(f"  Model:       {getattr(brain.reasoner, 'active_model_name', 'unknown')}")
        lines.append(f"  Plugins:     {len(brain.plugins.list_plugins())}")
        lines.append(f"  Skills:      {len(brain.skills.list_skills())}")
        lines.append(f"  MCP Servers: {len(brain.mcp.list_servers())}")
        lines.append(f"  MCP Tools:   {len(brain.mcp.list_tools())}")
        lines.append(f"  Permissions: {brain.permissions.level.name}")
        lines.append(f"  Memory:      {brain.memory.stats.get('lattice_nodes', 0)} nodes")
        active_tasks = brain.tasks.count(status_filter="in_progress")
        lines.append(f"  Active Tasks: {active_tasks}")
    else:
        lines.append("  Brain not available")
    return CommandResult(text="\n".join(lines))


@command("version", aliases=["ver"], description="Show JARVIS version and build info",
         usage="/version", category="core", permission=PermLevel.READ_ONLY)
async def cmd_version(ctx: CommandContext) -> CommandResult:
    import platform
    import sys
    lines = [
        "JARVIS v2.0.0",
        f"  Python:   {sys.version.split()[0]}",
        f"  Platform: {platform.platform()}",
        f"  Host:     {platform.node()}",
    ]
    return CommandResult(text="\n".join(lines))


@command("cost", aliases=["usage"], description="Show token usage and estimated cost",
         usage="/cost", category="core", permission=PermLevel.READ_ONLY)
async def cmd_cost(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    if brain and hasattr(brain, 'telemetry'):
        stats = brain.telemetry.get_session_stats() if hasattr(brain.telemetry, 'get_session_stats') else {}
        interactions = getattr(brain, '_interaction_count', 0)
        return CommandResult(text=f"Session interactions: {interactions}\nTelemetry: {stats}")
    return CommandResult(text="No usage data available yet.")


@command("model", aliases=["m"], description="Show or switch active LLM model",
         usage="/model [name] | /model list", category="core", permission=PermLevel.STANDARD)
async def cmd_model(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    args = ctx.args.strip().lower()
    if not brain:
        return CommandResult(text="Brain not available", success=False)

    providers = brain.reasoner.providers

    # No args: show current model
    if not args:
        model = getattr(brain.reasoner, 'active_model_name', 'unknown')
        current_provider = providers.get_active_providers()[0] if providers.get_active_providers() else None
        lines = [f"Current: {model}"]
        if current_provider:
            lines.append(f"Provider: {current_provider.name} ({current_provider.type})")
            lines.append(f"All models: {', '.join(current_provider.models)}")
        lines.append(f"\nUse /model list to see all options")
        return CommandResult(text="\n".join(lines))

    # List all available models
    if args == "list":
        lines = ["Available Models", "=" * 50]

        # Cloud providers
        for p in providers.get_active_providers():
            is_local = "localhost" in p.base_url or "127.0.0.1" in p.base_url
            source = "local" if is_local else "cloud"
            active = " (active)" if p.model == getattr(brain.reasoner, '_active_model', '') else ""
            lines.append(f"\n  {p.name} [{source}]{active}")
            for m in p.models:
                marker = " *" if m == p.model else ""
                lines.append(f"    {m}{marker}")

        # Check Ollama models
        try:
            import urllib.request, json
            resp = urllib.request.urlopen("http://localhost:11434/api/tags", timeout=2)
            data = json.loads(resp.read())
            ollama_models = [m["name"] for m in data.get("models", [])]
            if ollama_models:
                lines.append(f"\n  Ollama [local] — {len(ollama_models)} models")
                for m in ollama_models:
                    lines.append(f"    {m}")
        except Exception:
            pass

        lines.append(f"\nSwitch: /model <name>")
        lines.append(f"Shortcuts: /model haiku | /model sonnet | /model opus")
        return CommandResult(text="\n".join(lines))

    # Shortcuts for common models
    shortcuts = {
        "haiku": "claude-haiku-4-5-20251001",
        "sonnet": "claude-sonnet-4-20250514",
        "opus": "claude-opus-4-6-20250514",
        "gpt4": "gpt-4o",
        "gpt4mini": "gpt-4o-mini",
        "deepseek": "deepseek-chat",
        "deepseek-r1": "deepseek-reasoner",
    }
    target_model = shortcuts.get(args, args)

    # Try to switch within existing providers
    for p in providers.get_active_providers():
        if target_model in p.models or target_model == p.model:
            p.model = target_model
            providers._save()
            return CommandResult(text=f"Switched to: {target_model} ({p.name})")

    # Try Ollama
    try:
        import urllib.request, json
        resp = urllib.request.urlopen("http://localhost:11434/api/tags", timeout=2)
        data = json.loads(resp.read())
        ollama_models = [m["name"] for m in data.get("models", [])]
        if target_model in ollama_models or any(target_model in m for m in ollama_models):
            # Find or create Ollama provider
            matched = next((m for m in ollama_models if target_model in m), target_model)
            existing = None
            for p in providers.get_active_providers():
                if "localhost:11434" in p.base_url:
                    existing = p
                    break
            if existing:
                existing.model = matched
                existing.models = [matched]
                providers._save()
            else:
                providers.add_provider("ollama", "ollama", base_url="http://localhost:11434/v1", model=matched)
            return CommandResult(text=f"Switched to local: {matched} (Ollama)")
    except Exception:
        pass

    return CommandResult(text=f"Model not found: {args}\nUse /model list to see available models.", success=False)


@command("permissions", aliases=["perms"], description="Show or change permission level",
         usage="/permissions [read_only|standard|full|dangerous]", category="core", permission=PermLevel.STANDARD)
async def cmd_permissions(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    args = ctx.args.strip().lower()
    if not brain:
        return CommandResult(text="Brain not available", success=False)

    if not args:
        summary = brain.permissions.summary()
        lines = [f"Permission Level: {summary['level']}"]
        if summary['denied_tools']:
            lines.append(f"Denied tools: {', '.join(summary['denied_tools'])}")
        return CommandResult(text="\n".join(lines))

    from brain.permissions import PermissionLevel
    level_map = {
        "read_only": PermissionLevel.READ_ONLY, "readonly": PermissionLevel.READ_ONLY,
        "standard": PermissionLevel.STANDARD,
        "full": PermissionLevel.FULL,
        "dangerous": PermissionLevel.DANGEROUS_FULL,
    }
    level = level_map.get(args)
    if level is None:
        return CommandResult(text=f"Unknown level: {args}. Use: read_only, standard, full, dangerous", success=False)
    brain.permissions.set_level(level)
    return CommandResult(text=f"Permission level set to: {level.name}")


@command("config", aliases=["cfg"], description="Inspect or edit JARVIS config",
         usage="/config [key] [value]", category="core", permission=PermLevel.STANDARD)
async def cmd_config(ctx: CommandContext) -> CommandResult:
    from brain.config import JARVIS_HOME, DATA_DIR, GROQ_MODEL, LOCAL_MODEL, STT_MODEL, TTS_MODEL
    lines = [
        "JARVIS Configuration",
        f"  JARVIS_HOME: {JARVIS_HOME}",
        f"  DATA_DIR:    {DATA_DIR}",
        f"  GROQ_MODEL:  {GROQ_MODEL}",
        f"  LOCAL_MODEL: {LOCAL_MODEL}",
        f"  STT_MODEL:   {STT_MODEL}",
        f"  TTS_MODEL:   {TTS_MODEL}",
    ]
    return CommandResult(text="\n".join(lines))


@command("clear", aliases=["cls"], description="Clear screen or start fresh session",
         usage="/clear", category="core", permission=PermLevel.READ_ONLY)
async def cmd_clear(ctx: CommandContext) -> CommandResult:
    return CommandResult(text="", action="clear")


@command("exit", aliases=["quit", "q"], description="Exit JARVIS",
         usage="/exit", category="core", permission=PermLevel.READ_ONLY)
async def cmd_exit(ctx: CommandContext) -> CommandResult:
    return CommandResult(text="Goodbye.", action="exit")


@command("compact", description="Compact conversation context to free tokens",
         usage="/compact", category="core", permission=PermLevel.STANDARD)
async def cmd_compact(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    if brain:
        from brain.agent.context import token_usage_display
        history = brain.memory.get_history(limit=50)
        msgs = [{"role": "user" if h["role"] == "user" else "assistant", "content": h["content"]} for h in history]
        display = token_usage_display(msgs, getattr(brain.reasoner, 'active_model_name', ''))
        return CommandResult(text=f"Context usage: {display}")
    return CommandResult(text="No context to compact.")


@command("mode", description="Switch mode (normal/agent/plan/berbon/cli)",
         usage="/mode [normal|agent|plan|berbon|cli]", category="core", permission=PermLevel.STANDARD)
async def cmd_mode(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    args = ctx.args.strip().lower()
    if not brain:
        return CommandResult(text="Brain not available", success=False)

    valid_modes = {"normal", "agent", "plan", "berbon", "cli", "mobile"}
    if not args:
        return CommandResult(text=f"Current mode: {brain.mode}\nAvailable: {', '.join(sorted(valid_modes))}")

    if args not in valid_modes:
        return CommandResult(text=f"Unknown mode: {args}. Use: {', '.join(sorted(valid_modes))}", success=False)

    brain.mode = args
    if args == "plan":
        brain.permissions.set_level(0)  # READ_ONLY
    elif brain.permissions.level == 0 and args != "plan":
        brain.permissions.set_level(2)  # FULL
    return CommandResult(text=f"Mode switched to: {args}")
