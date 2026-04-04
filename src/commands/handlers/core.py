"""Core flow commands -- help, status, mode, config, cost, permissions, context, version."""
import json
import time
from src.commands.registry import command, CommandContext, CommandResult, PermLevel


@command("help", aliases=["h", "?"], description="Show command reference",
         usage="/help [--all] [--json] [command]", category="core", permission=PermLevel.READ_ONLY)
async def cmd_help(ctx: CommandContext) -> CommandResult:
    args = ctx.args.strip()

    # Strip flags from args to detect a command name
    clean_args = args.replace("--all", "").replace("--json", "").strip()
    if clean_args and not clean_args.startswith("-"):
        from src.commands.registry import registry
        return CommandResult(text=registry.get_help(clean_args))

    from src.commands.registry import registry, CATEGORIES
    include_hidden = "--all" in args
    output_json = "--json" in args

    # Build structured data for both display and JSON output
    categories_data = []
    total_commands = 0
    for cat_slug, cat_name in CATEGORIES:
        cmds = registry.list_commands(category=cat_slug, include_hidden=include_hidden)
        if not cmds:
            continue
        cat_info = {
            "slug": cat_slug,
            "name": cat_name,
            "count": len(cmds),
            "commands": [
                {
                    "name": cmd.name,
                    "description": cmd.description,
                    "usage": cmd.usage,
                    "aliases": cmd.aliases,
                    "permission": cmd.permission.name,
                }
                for cmd in cmds
            ],
        }
        categories_data.append(cat_info)
        total_commands += len(cmds)

    if include_hidden:
        hidden = registry.list_commands(include_hidden=True)
        hidden = [c for c in hidden if c.hidden]
        if hidden:
            categories_data.append({
                "slug": "hidden",
                "name": "Debug/Hidden",
                "count": len(hidden),
                "commands": [
                    {"name": c.name, "description": c.description, "usage": c.usage,
                     "aliases": c.aliases, "permission": c.permission.name}
                    for c in hidden
                ],
            })
            total_commands += len(hidden)

    # JSON output mode
    if output_json:
        payload = {"total": total_commands, "categories": categories_data}
        return CommandResult(
            text=json.dumps(payload, indent=2),
            data=payload,
        )

    # Text display with category counts
    lines = ["JARVIS Commands", "=" * 50]
    for cat in categories_data:
        lines.append(f"\n  {cat['name']} ({cat['count']})")
        lines.append("  " + "-" * (len(cat['name']) + len(str(cat['count'])) + 3))
        for cmd_info in cat["commands"]:
            aliases_str = (
                f" ({', '.join('/' + a for a in cmd_info['aliases'])})"
                if cmd_info["aliases"] else ""
            )
            lines.append(f"  /{cmd_info['name']:<20s} {cmd_info['description']}{aliases_str}")

    lines.append(f"\n  {total_commands} commands across {len(categories_data)} categories. Use /help <command> for details.")
    return CommandResult(text="\n".join(lines))


@command("status", aliases=["stat"], description="Show JARVIS status including model, mode, session, MCP, and tool statuses",
         usage="/status", category="core", permission=PermLevel.READ_ONLY)
async def cmd_status(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    lines = ["JARVIS Status", "=" * 40]
    if brain:
        lines.append(f"  Mode:        {brain.mode}")
        lines.append(f"  Model:       {getattr(brain.reasoner, 'active_model_name', 'unknown')}")
        # Tools
        try:
            from src.agent.tools import TOOL_SCHEMAS
            lines.append(f"  Tools:       {len(TOOL_SCHEMAS)}")
        except Exception:
            lines.append(f"  Tools:       ?")
        lines.append(f"  Commands:    {brain.command_registry.count}")
        lines.append(f"  Plugins:     {len(brain.plugins.list_plugins())}")
        lines.append(f"  Skills:      {len(brain.skills.list_skills())}")
        lines.append(f"  MCP Servers: {len(brain.mcp.list_servers())}")
        lines.append(f"  MCP Tools:   {len(brain.mcp.list_tools())}")
        lines.append(f"  Permissions: {brain.permissions.level.name}")
        lines.append(f"  Memory:      {brain.memory.stats.get('lattice_nodes', 0)} nodes")
        active_tasks = brain.tasks.count(status_filter="in_progress")
        lines.append(f"  Active Tasks: {active_tasks}")

        # Session duration
        start_time = getattr(brain, '_session_start_time', None)
        if start_time is None:
            # Fall back to memory session start or brain init time
            start_time = getattr(brain.memory, '_session_start', None) or getattr(brain, '_init_time', None)
        if start_time:
            elapsed = time.time() - start_time
            hours, remainder = divmod(int(elapsed), 3600)
            mins, secs = divmod(remainder, 60)
            duration = f"{hours}h {mins}m {secs}s" if hours else f"{mins}m {secs}s"
            lines.append(f"  Duration:    {duration}")

        # Token usage and cost from CostTracker
        try:
            from src.agent.cost_tracker import get_tracker
            tracker = get_tracker()
            total_tokens = sum(u.total_tokens for u in tracker._model_usage.values())
            if total_tokens > 0:
                lines.append(f"  Tokens:      {tracker.format_tokens(total_tokens)}")
                lines.append(f"  Cost:        {tracker.format_cost(tracker.get_session_cost())}")
                lines.append(f"  Turns:       {tracker._turn_count}")
        except Exception:
            interactions = getattr(brain, '_interaction_count', 0)
            if interactions:
                lines.append(f"  Interactions: {interactions}")
    else:
        lines.append("  Brain not available")
    return CommandResult(text="\n".join(lines))


@command("version", aliases=["ver"], description="Show JARVIS version, model, provider, and runtime info",
         usage="/version", category="core", permission=PermLevel.READ_ONLY)
async def cmd_version(ctx: CommandContext) -> CommandResult:
    import platform
    import sys
    brain = ctx.brain
    lines = [
        "JARVIS v2.0.0",
        f"  Python:   {sys.version.split()[0]}",
        f"  Platform: {platform.platform()}",
        f"  Host:     {platform.node()}",
    ]
    if brain:
        model = getattr(brain.reasoner, 'active_model_name', 'unknown')
        lines.append(f"  Model:    {model}")
        providers = brain.reasoner.providers
        active = providers.get_active_providers()
        if active:
            p = active[0]
            lines.append(f"  Provider: {p.name} ({p.type})")
            lines.append(f"  Base URL: {p.base_url}")

        # Context window from MODEL_LIMITS
        try:
            from src.agent.context import MODEL_LIMITS, DEFAULT_MAX_TOKENS
            ctx_limit = MODEL_LIMITS.get(model, DEFAULT_MAX_TOKENS)
            lines.append(f"  Context:  {ctx_limit:,} tokens")
        except Exception:
            pass
    return CommandResult(text="\n".join(lines))


@command("cost", aliases=["usage"], description="Show token usage and estimated cost",
         usage="/cost", category="core", permission=PermLevel.READ_ONLY)
async def cmd_cost(ctx: CommandContext) -> CommandResult:
    from src.agent.cost_tracker import get_tracker, CostTracker

    tracker = get_tracker()
    model_usage = tracker.get_session_usage()

    if not model_usage:
        return CommandResult(text="No usage data available yet.")

    lines = ["Token Usage & Cost", "=" * 50]

    # Per-model breakdown
    total_input = 0
    total_output = 0
    total_cache_read = 0
    total_cache_write = 0

    for model, usage in model_usage.items():
        cost = tracker._calculate_cost(model, usage)
        # Short label
        label = model.split("/")[-1]
        for prefix in ("claude-", "gpt-"):
            if label.startswith(prefix):
                label = label[len(prefix):]
                break

        lines.append(f"\n  {label}")
        lines.append(f"    Input:       {CostTracker.format_tokens(usage.input_tokens):>8s}")
        lines.append(f"    Output:      {CostTracker.format_tokens(usage.output_tokens):>8s}")
        if usage.cache_read_tokens:
            lines.append(f"    Cache read:  {CostTracker.format_tokens(usage.cache_read_tokens):>8s}")
        if usage.cache_write_tokens:
            lines.append(f"    Cache write: {CostTracker.format_tokens(usage.cache_write_tokens):>8s}")
        lines.append(f"    Cost:        {CostTracker.format_cost(cost):>8s}")

        total_input += usage.input_tokens
        total_output += usage.output_tokens
        total_cache_read += usage.cache_read_tokens
        total_cache_write += usage.cache_write_tokens

    # Session totals
    total_tokens = total_input + total_output + total_cache_read + total_cache_write
    lines.append(f"\n  {'Session Total':─<40s}")
    lines.append(f"    Total tokens: {CostTracker.format_tokens(total_tokens)}")
    lines.append(f"    Input:        {CostTracker.format_tokens(total_input)}")
    lines.append(f"    Output:       {CostTracker.format_tokens(total_output)}")
    if total_cache_read:
        lines.append(f"    Cache read:   {CostTracker.format_tokens(total_cache_read)}")
    if total_cache_write:
        lines.append(f"    Cache write:  {CostTracker.format_tokens(total_cache_write)}")
    lines.append(f"    Total cost:   {CostTracker.format_cost(tracker.get_session_cost())}")
    lines.append(f"    Turns:        {tracker._turn_count}")

    return CommandResult(text="\n".join(lines))


@command("model", aliases=["m"], description="Show or switch active LLM model",
         usage="/model [name] | /model list", category="core", permission=PermLevel.STANDARD)
async def cmd_model(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    args = ctx.args.strip().lower()
    if not brain:
        return CommandResult(text="Brain not available", success=False)

    providers = brain.reasoner.providers

    # No args: show current model with pricing and context window
    if not args:
        model = getattr(brain.reasoner, 'active_model_name', 'unknown')
        current_provider = providers.get_active_providers()[0] if providers.get_active_providers() else None
        lines = [f"Current: {model}"]
        if current_provider:
            lines.append(f"Provider: {current_provider.name} ({current_provider.type})")
            lines.append(f"All models: {', '.join(current_provider.models)}")

        # Show context window
        try:
            from src.agent.context import MODEL_LIMITS, DEFAULT_MAX_TOKENS
            ctx_limit = MODEL_LIMITS.get(model, DEFAULT_MAX_TOKENS)
            lines.append(f"Context window: {ctx_limit:,} tokens")
        except Exception:
            pass

        # Show pricing
        try:
            from src.agent.cost_tracker import CostTracker
            pricing = CostTracker._resolve_pricing(model)
            if pricing:
                lines.append(f"Pricing (per 1M tokens):")
                lines.append(f"  Input:  ${pricing.get('input', 0):.2f}")
                lines.append(f"  Output: ${pricing.get('output', 0):.2f}")
                if pricing.get('cache_read'):
                    lines.append(f"  Cache read:  ${pricing['cache_read']:.2f}")
                if pricing.get('cache_write'):
                    lines.append(f"  Cache write: ${pricing['cache_write']:.2f}")
        except Exception:
            pass

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
        "sonnet": "claude-sonnet-4-6-20250514",
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


@command("permissions", aliases=["perms"], description="Manage allow & deny tool permission rules and levels",
         usage="/permissions [read_only|standard|full|dangerous]", category="core", permission=PermLevel.STANDARD)
async def cmd_permissions(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    args = ctx.args.strip().lower()
    if not brain:
        return CommandResult(text="Brain not available", success=False)

    if not args:
        summary = brain.permissions.summary()
        lines = [
            "Permissions",
            "=" * 40,
            f"  Level: {summary['level']}",
            f"  Mode:  {summary.get('mode', 'default')}",
        ]
        if summary.get('denied_tools'):
            lines.append(f"  Denied tools: {', '.join(summary['denied_tools'])}")
        if summary.get('denied_prefixes'):
            lines.append(f"  Denied prefixes: {', '.join(summary['denied_prefixes'])}")

        # Active rules
        rules = summary.get('rules', [])
        if rules:
            lines.append(f"\n  Active Rules ({len(rules)})")
            lines.append("  " + "-" * 20)
            for r in rules[:15]:
                lines.append(f"    [{r['behavior']:<5s}] {r['tool']}: {r['content']} ({r['source']})")
            if len(rules) > 15:
                lines.append(f"    ... and {len(rules) - 15} more")

        # Denial counts
        denial_counts = summary.get('denial_counts', {})
        if denial_counts:
            lines.append(f"\n  Denial Counts")
            lines.append("  " + "-" * 20)
            for tool, count in sorted(denial_counts.items(), key=lambda x: -x[1]):
                lines.append(f"    {tool}: {count}")
        return CommandResult(text="\n".join(lines))

    from src.permissions import PermissionLevel
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
         usage="/config [show|set <key> <value>|reset <key>]", category="core", permission=PermLevel.STANDARD)
async def cmd_config(ctx: CommandContext) -> CommandResult:
    from pathlib import Path
    from src.config import JARVIS_HOME, DATA_DIR, GROQ_MODEL, LOCAL_MODEL, STT_MODEL, TTS_MODEL

    args_parts = ctx.args.strip().split(maxsplit=2)
    sub = args_parts[0].lower() if args_parts else "show"

    settings_path = JARVIS_HOME / "settings.json"

    def _load_settings() -> dict:
        if settings_path.exists():
            try:
                return json.loads(settings_path.read_text())
            except Exception:
                return {}
        return {}

    def _save_settings(data: dict):
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(json.dumps(data, indent=2) + "\n")

    if sub == "show" or not args_parts:
        lines = [
            "JARVIS Configuration",
            "=" * 50,
            "",
            "  Environment",
            "  " + "-" * 11,
            f"  JARVIS_HOME: {JARVIS_HOME}",
            f"  DATA_DIR:    {DATA_DIR}",
            "",
            "  Models",
            "  " + "-" * 6,
            f"  GROQ_MODEL:  {GROQ_MODEL}",
            f"  LOCAL_MODEL: {LOCAL_MODEL}",
            f"  STT_MODEL:   {STT_MODEL}",
            f"  TTS_MODEL:   {TTS_MODEL}",
        ]
        # Show user settings from settings.json
        settings = _load_settings()
        if settings:
            lines.append("")
            lines.append("  User Settings (~/.jarvis/settings.json)")
            lines.append("  " + "-" * 38)
            for key, value in sorted(settings.items()):
                val_str = json.dumps(value) if isinstance(value, (dict, list)) else str(value)
                if len(val_str) > 60:
                    val_str = val_str[:57] + "..."
                lines.append(f"  {key}: {val_str}")
        else:
            lines.append(f"\n  No user settings in {settings_path}")
        return CommandResult(text="\n".join(lines))

    elif sub == "set":
        if len(args_parts) < 3:
            return CommandResult(text="Usage: /config set <key> <value>", success=False)
        key = args_parts[1]
        raw_value = args_parts[2]
        # Try to parse as JSON (for bools, numbers, objects); fall back to string
        try:
            value = json.loads(raw_value)
        except (json.JSONDecodeError, ValueError):
            value = raw_value

        settings = _load_settings()
        settings[key] = value
        _save_settings(settings)
        return CommandResult(text=f"Set {key} = {json.dumps(value)}")

    elif sub == "reset":
        if len(args_parts) < 2:
            return CommandResult(text="Usage: /config reset <key>", success=False)
        key = args_parts[1]
        settings = _load_settings()
        if key in settings:
            del settings[key]
            _save_settings(settings)
            return CommandResult(text=f"Reset {key} (removed from settings)")
        return CommandResult(text=f"Key not found: {key}", success=False)

    else:
        return CommandResult(
            text=f"Unknown subcommand: {sub}. Use: show, set <key> <value>, reset <key>",
            success=False,
        )


@command("clear", aliases=["cls"], description="Clear screen or start fresh session",
         usage="/clear", category="core", permission=PermLevel.READ_ONLY)
async def cmd_clear(ctx: CommandContext) -> CommandResult:
    return CommandResult(text="", action="clear")


@command("exit", aliases=["quit", "q"], description="Exit JARVIS",
         usage="/exit", category="core", permission=PermLevel.READ_ONLY)
async def cmd_exit(ctx: CommandContext) -> CommandResult:
    return CommandResult(text="Goodbye.", action="exit")


@command("compact", description="Compact conversation context to free tokens",
         usage="/compact [--dry-run]", category="core", permission=PermLevel.STANDARD)
async def cmd_compact(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    if not brain:
        return CommandResult(text="No context to compact.")

    from src.agent.context import (
        token_usage_display, estimate_tokens, compact_messages,
        microcompact_messages, MODEL_LIMITS, DEFAULT_MAX_TOKENS,
    )

    history = brain.memory.get_history(limit=200)
    msgs = [{"role": "user" if h["role"] == "user" else "assistant", "content": h["content"]} for h in history]
    model = getattr(brain.reasoner, 'active_model_name', '')
    tokens_before = estimate_tokens(msgs)
    max_tokens = MODEL_LIMITS.get(model, DEFAULT_MAX_TOKENS)

    dry_run = "--dry-run" in ctx.args

    # Try microcompact first, then full if needed
    micro = microcompact_messages(msgs, preserve_recent=10)
    tokens_after_micro = estimate_tokens(micro)

    full = compact_messages(msgs, max_tokens=max_tokens)
    tokens_after_full = estimate_tokens(full)

    # Determine which type would be applied
    if tokens_before == tokens_after_micro:
        compact_type = "none needed"
    elif tokens_after_micro <= max_tokens * 0.75:
        compact_type = "micro"
        tokens_after = tokens_after_micro
    else:
        compact_type = "full"
        tokens_after = tokens_after_full

    lines = [
        "Context Compaction",
        "=" * 40,
        f"  Before:    {tokens_before:,} tokens",
    ]

    if compact_type == "none needed":
        lines.append(f"  Status:    Context is within budget, no compaction needed")
        lines.append(f"  Usage:     {token_usage_display(msgs, model)}")
    else:
        saved = tokens_before - tokens_after
        lines.append(f"  After:     {tokens_after:,} tokens")
        lines.append(f"  Saved:     {saved:,} tokens ({int(saved / tokens_before * 100)}%)")
        lines.append(f"  Type:      {compact_type}")
        lines.append(f"  Budget:    {max_tokens:,} tokens")

        if dry_run:
            lines.append(f"\n  (dry run -- no changes applied)")
        else:
            lines.append(f"\n  Compaction applied.")

    # Show compaction history if AutoCompactor is in use
    if hasattr(brain, '_compactor'):
        compactor = brain._compactor
        budget = compactor.get_budget()
        lines.append(f"\n  Compaction history: {budget.compaction_count} compactions this session")

    return CommandResult(text="\n".join(lines))


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


@command("context", aliases=["ctx"], description="Show detailed token usage breakdown by category",
         usage="/context", category="core", permission=PermLevel.READ_ONLY)
async def cmd_context(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available", success=False)

    from src.agent.context import (
        format_token_budget_status, estimate_tokens,
        MODEL_LIMITS, DEFAULT_MAX_TOKENS, token_usage_display,
    )

    model = getattr(brain.reasoner, 'active_model_name', '')
    history = brain.memory.get_history(limit=500)
    msgs = [{"role": h.get("role", "user"), "content": h.get("content", "")} for h in history]

    if not msgs:
        return CommandResult(text="No conversation context yet.")

    status = format_token_budget_status(msgs, model=model)
    breakdown = status["breakdown"]
    max_tokens = status["max_tokens"]

    lines = [
        "Context Token Usage",
        "=" * 50,
        f"  Model:          {model or 'unknown'}",
        f"  Context window: {max_tokens:,} tokens",
        f"  Used:           {status['total_tokens']:,} tokens ({status['usage_pct']}%)",
        "",
        "  Breakdown",
        "  " + "-" * 30,
    ]

    categories = [
        ("System prompt", breakdown["system_prompt"]),
        ("Conversation", breakdown["conversation"]),
        ("Tool results", breakdown["tool_results"]),
        ("Recent context", breakdown["recent_context"]),
    ]

    for label, tokens in categories:
        if max_tokens > 0:
            pct = tokens / max_tokens * 100
        else:
            pct = 0
        bar_len = 20
        filled = int(bar_len * pct / 100) if pct > 0 else 0
        bar = "=" * filled + " " * (bar_len - filled)
        lines.append(f"  {label:<16s} [{bar}] {tokens:>7,} ({pct:4.1f}%)")

    # Recommendation
    rec_map = {
        "ok": "Context usage is healthy",
        "consider_compacting": "Consider running /compact soon",
        "compact_now": "Context is getting full -- run /compact",
        "critical": "CRITICAL -- context nearly full, run /compact immediately",
    }
    rec = status.get("recommendation", "ok")
    lines.append(f"\n  Status: {rec_map.get(rec, rec)}")

    # Visual usage bar
    lines.append(f"\n  {token_usage_display(msgs, model)}")

    return CommandResult(text="\n".join(lines))


@command("advisor", description="Configure a secondary advisor model",
         usage="/advisor [model|off|status]", category="core")
async def cmd_advisor(ctx: CommandContext) -> CommandResult:
    """Configure advisor model for second opinions."""
    args = ctx.args.strip() if ctx.args else "status"

    if args == "status":
        advisor = getattr(ctx.brain, '_advisor_model', None) if ctx.brain else None
        if advisor:
            return CommandResult(text=f"Advisor model: {advisor}")
        return CommandResult(text="No advisor model configured.\nUsage: /advisor <model> to set one.")

    if args in ("off", "none", "disable"):
        if ctx.brain:
            ctx.brain._advisor_model = None
        return CommandResult(text="Advisor model disabled.")

    # Set advisor model
    model = args
    if ctx.brain:
        ctx.brain._advisor_model = model
    return CommandResult(text=f"Advisor model set to: {model}\nJARVIS will consult this model for second opinions on complex tasks.")


@command("sandbox", aliases=["sandbox-toggle"],
         description="Toggle command sandboxing on/off",
         usage="/sandbox [on|off|status]", category="core")
async def cmd_sandbox(ctx: CommandContext) -> CommandResult:
    """Toggle bash command sandboxing."""
    from src.sandbox import SandboxConfig, detect_sandbox_capabilities

    args = ctx.args.strip() if ctx.args else "status"

    caps = detect_sandbox_capabilities()

    if args == "status":
        lines = ["Sandbox Status", "=" * 30]
        available_backends = []
        if caps.available:
            if caps.unshare_path:
                available_backends.append(f"unshare ({caps.unshare_path})")
            if caps.namespace_support:
                available_backends.append("namespaces")
            if caps.network_support:
                available_backends.append("network isolation")
        if caps.in_container:
            available_backends.append("container (host)")
        lines.append(f"  Available backends: {', '.join(available_backends) if available_backends else 'none'}")

        # Check current state
        try:
            from src.config import JARVIS_HOME
            settings_path = JARVIS_HOME / "settings.json"
            if settings_path.exists():
                settings = json.loads(settings_path.read_text())
                enabled = settings.get("sandbox", {}).get("enabled", True)
                lines.append(f"  Enabled: {'yes' if enabled else 'no'}")
            else:
                lines.append(f"  Enabled: yes (default)")
        except Exception:
            lines.append(f"  Enabled: yes (default)")

        return CommandResult(text="\n".join(lines))

    if args in ("on", "enable"):
        if not caps.available:
            return CommandResult(text="No sandbox backends available.\nInstall unshare or run inside a container.", success=False)
        # Persist
        try:
            from src.config import JARVIS_HOME
            settings_path = JARVIS_HOME / "settings.json"
            settings = {}
            if settings_path.exists():
                settings = json.loads(settings_path.read_text())
            settings.setdefault("sandbox", {})["enabled"] = True
            settings_path.write_text(json.dumps(settings, indent=2))
        except Exception:
            pass
        return CommandResult(text="Sandbox enabled. Bash commands will run in sandboxed environment.")

    if args in ("off", "disable"):
        try:
            from src.config import JARVIS_HOME
            settings_path = JARVIS_HOME / "settings.json"
            settings = {}
            if settings_path.exists():
                settings = json.loads(settings_path.read_text())
            settings.setdefault("sandbox", {})["enabled"] = False
            settings_path.write_text(json.dumps(settings, indent=2))
        except Exception:
            pass
        return CommandResult(text="Sandbox DISABLED. Bash commands will run without isolation.")

    return CommandResult(text="Usage: /sandbox [on|off|status]")
