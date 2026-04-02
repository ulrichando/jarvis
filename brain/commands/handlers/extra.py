"""Extra commands — additional Claude Code-style commands for JARVIS."""
import os
import time
import subprocess
from brain.commands.registry import command, CommandContext, CommandResult, PermLevel


@command("desktop", description="Launch JARVIS desktop app (transparent window)",
         usage="/desktop", category="core", permission=PermLevel.STANDARD)
async def cmd_desktop(ctx: CommandContext) -> CommandResult:
    jarvis_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    subprocess.Popen(
        ["python3", "-m", "shells.desktop"],
        cwd=jarvis_root,
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return CommandResult(text="JARVIS desktop launching...")


@command("add-dir", description="Add a working directory to the session",
         usage="/add-dir <path>", category="core", permission=PermLevel.STANDARD)
async def cmd_add_dir(ctx: CommandContext) -> CommandResult:
    path = ctx.args.strip()
    if not path:
        return CommandResult(text="Usage: /add-dir <path>", success=False)
    import os
    expanded = os.path.expanduser(path)
    if not os.path.isdir(expanded):
        return CommandResult(text=f"Not a directory: {expanded}", success=False)
    os.chdir(expanded)
    return CommandResult(text=f"Working directory changed to: {expanded}")


@command("context", description="Show current context window usage",
         usage="/context", category="core", permission=PermLevel.READ_ONLY)
async def cmd_context(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available", success=False)
    from brain.agent.context import estimate_tokens, MODEL_LIMITS
    history = brain.memory.get_history(limit=50)
    msgs = [{"role": "user" if h["role"] == "user" else "assistant",
             "content": h["content"]} for h in history]
    total = estimate_tokens(msgs)
    model = getattr(brain.reasoner, 'active_model_name', '')
    limit = MODEL_LIMITS.get(model, 24000)
    pct = min(100, int(total / limit * 100))
    # Build visual bar
    filled = pct // 5
    empty = 20 - filled
    bar = "\u2588" * filled + "\u2591" * empty
    lines = [
        f"Context Usage: {bar} {pct}%",
        f"  Tokens: ~{total:,} / {limit:,}",
        f"  Model: {model}",
        f"  History turns: {len(history)}",
        f"  Lattice nodes: {brain.memory.stats.get('lattice_nodes', 0)}",
    ]
    return CommandResult(text="\n".join(lines))


@command("copy", description="Copy last response to clipboard",
         usage="/copy [N]", category="core", permission=PermLevel.READ_ONLY)
async def cmd_copy(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available", success=False)
    n = 1
    if ctx.args.strip().isdigit():
        n = int(ctx.args.strip())
    history = brain.memory.get_history(limit=50)
    jarvis_msgs = [h for h in history if h["role"] == "jarvis"]
    if not jarvis_msgs or n > len(jarvis_msgs):
        return CommandResult(text="No response to copy.", success=False)
    content = jarvis_msgs[-n]["content"]
    import subprocess
    try:
        subprocess.run(["xclip", "-selection", "clipboard"],
                       input=content.encode(), check=True, timeout=5)
        return CommandResult(text=f"Copied {len(content)} chars to clipboard.")
    except Exception:
        try:
            subprocess.run(["xsel", "--clipboard", "--input"],
                           input=content.encode(), check=True, timeout=5)
            return CommandResult(text=f"Copied {len(content)} chars to clipboard.")
        except Exception:
            return CommandResult(
                text="Clipboard tools not found. Install xclip or xsel.",
                success=False,
            )


@command("doctor", description="Diagnose JARVIS installation and settings",
         usage="/doctor", category="core", permission=PermLevel.READ_ONLY)
async def cmd_doctor(ctx: CommandContext) -> CommandResult:
    import shutil
    import platform
    import sys
    import os
    lines = ["JARVIS Doctor", "=" * 40]
    # Python
    lines.append(f"  Python:     {sys.version.split()[0]} \u2714")
    lines.append(f"  Platform:   {platform.platform()}")
    # API keys
    groq = bool(os.environ.get("GROQ_API_KEY"))
    anthropic = bool(os.environ.get("ANTHROPIC_API_KEY"))
    openai = bool(os.environ.get("OPENAI_API_KEY"))
    lines.append(f"  Groq API:   {'\u2714' if groq else '\u2718 (set GROQ_API_KEY)'}")
    lines.append(f"  Anthropic:  {'\u2714' if anthropic else '\u2014 (optional)'}")
    lines.append(f"  OpenAI:     {'\u2714' if openai else '\u2014 (optional)'}")
    # Ollama
    try:
        import urllib.request
        urllib.request.urlopen("http://localhost:11434/api/tags", timeout=2)
        lines.append("  Ollama:     \u2714 (running)")
    except Exception:
        lines.append("  Ollama:     \u2718 (not running)")
    # Tools
    for tool in ["git", "nmap", "xclip", "jq"]:
        path = shutil.which(tool)
        lines.append(f"  {tool:<10s}  {'\u2714' if path else '\u2718'}")
    # Brain modules
    brain = ctx.brain
    if brain:
        lines.append(f"\n  Commands:   {brain.command_registry.visible_count}")
        lines.append(f"  Plugins:    {len(brain.plugins.list_plugins())}")
        lines.append(f"  Skills:     {len(brain.skills.list_skills())}")
        lines.append(f"  MCP:        {len(brain.mcp.list_servers())} servers, {len(brain.mcp.list_tools())} tools")
        lines.append(f"  Memory:     {brain.memory.stats.get('lattice_nodes', 0)} nodes")
    return CommandResult(text="\n".join(lines))


@command("rename", description="Rename the current session",
         usage="/rename <name>", category="session", permission=PermLevel.STANDARD)
async def cmd_rename(ctx: CommandContext) -> CommandResult:
    name = ctx.args.strip()
    if not name:
        return CommandResult(text="Usage: /rename <new-name>", success=False)
    mgr = ctx.session_mgr
    if mgr and mgr.current:
        mgr.current.name = name
        mgr.save_current()
        return CommandResult(text=f"Session renamed to: {name}")
    return CommandResult(text="No active session.", success=False)


@command("login", description="Authenticate with an API provider",
         usage="/login [provider]", category="core", permission=PermLevel.STANDARD)
async def cmd_login(ctx: CommandContext) -> CommandResult:
    provider = ctx.args.strip() or "groq"
    return CommandResult(
        text=f"To add a {provider} API key:\n"
             f"  1. Set the env var (e.g., GROQ_API_KEY=...)\n"
             f"  2. Or add to ~/.jarvis/.env\n"
             f"  3. Or use: /config to view current providers\n\n"
             f"Run /doctor to verify."
    )


@command("logout", description="Clear saved credentials for a provider",
         usage="/logout [provider]", category="core", permission=PermLevel.STANDARD)
async def cmd_logout(ctx: CommandContext) -> CommandResult:
    provider = ctx.args.strip()
    if not provider:
        return CommandResult(text="Usage: /logout <provider>", success=False)
    brain = ctx.brain
    if brain:
        brain.vault.delete(provider)
        from brain.oauth import clear_credentials
        clear_credentials(provider)
        return CommandResult(text=f"Credentials cleared for: {provider}")
    return CommandResult(text="Brain not available", success=False)


@command("verbose", description="Toggle verbose output mode",
         usage="/verbose", category="core", permission=PermLevel.READ_ONLY, hidden=True)
async def cmd_verbose(ctx: CommandContext) -> CommandResult:
    import logging
    root = logging.getLogger("jarvis")
    if root.level <= logging.DEBUG:
        root.setLevel(logging.INFO)
        return CommandResult(text="Verbose mode: OFF")
    else:
        root.setLevel(logging.DEBUG)
        return CommandResult(text="Verbose mode: ON (debug logging enabled)")


@command("fast", description="Toggle fast/compact response mode",
         usage="/fast", category="core", permission=PermLevel.READ_ONLY)
async def cmd_fast(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    if brain:
        brain._fast_mode = not getattr(brain, '_fast_mode', False)
        return CommandResult(text=f"Fast mode: {'ON' if brain._fast_mode else 'OFF'}")
    return CommandResult(text="Brain not available", success=False)


@command("theme", description="Show or set color theme",
         usage="/theme [dark|light|cyber]", category="core", permission=PermLevel.READ_ONLY)
async def cmd_theme(ctx: CommandContext) -> CommandResult:
    return CommandResult(
        text="Themes: dark (default), light, cyber\n"
             "Theme switching not yet implemented \u2014 JARVIS uses dark by default."
    )


@command("tips", description="Show usage tips",
         usage="/tips", category="core", permission=PermLevel.READ_ONLY)
async def cmd_tips(ctx: CommandContext) -> CommandResult:
    tips = [
        "! for shell: !ls -la runs directly",
        "!! for analysis: !!netstat pipes output to JARVIS",
        "@ for files: mention files inline (coming soon)",
        "& for background: run long tasks without blocking",
        "\\ + Enter for multi-line input",
        "/scout <task> for read-only exploration",
        "/worker <task> for full-access execution",
        "/plan <task> for structured planning",
        "/ultraplan <task> for deep research + planning",
        "/team 'task' scout,planner,worker for coordinated agents",
        "/mcp to see connected tool servers",
        "/serve to expose JARVIS as an MCP server",
        "/doctor to check your installation",
        "/context to see token usage",
        "Ctrl+C to cancel, Ctrl+D to exit",
    ]
    return CommandResult(
        text="JARVIS Tips\n" + "=" * 30 + "\n" + "\n".join(f"  \u2022 {t}" for t in tips)
    )


@command("keybindings", description="Show keyboard shortcuts",
         usage="/keybindings", category="core", permission=PermLevel.READ_ONLY)
async def cmd_keybindings(ctx: CommandContext) -> CommandResult:
    bindings = [
        ("!", "Shell mode \u2014 run command directly"),
        ("!!", "Shell + analyze \u2014 run and pipe to JARVIS"),
        ("/", "Commands \u2014 slash command menu"),
        ("Ctrl+C", "Cancel current operation"),
        ("Ctrl+D", "Exit JARVIS"),
        ("Up/Down", "Navigate command history"),
    ]
    lines = ["Keyboard Shortcuts", "=" * 40]
    for key, desc in bindings:
        lines.append(f"  {key:<14s}  {desc}")
    return CommandResult(text="\n".join(lines))


@command("feedback", description="Report an issue or give feedback",
         usage="/feedback", category="core", permission=PermLevel.READ_ONLY)
async def cmd_feedback(ctx: CommandContext) -> CommandResult:
    return CommandResult(
        text="JARVIS Feedback\n"
             "  GitHub: https://github.com/ulrich/jarvis\n"
             "  Report bugs, request features, or contribute.\n"
             "  Or just tell me what's broken \u2014 I'll file it myself."
    )


@command("stash", description="Stash current conversation for later",
         usage="/stash [name]", category="session", permission=PermLevel.STANDARD)
async def cmd_stash(ctx: CommandContext) -> CommandResult:
    mgr = ctx.session_mgr
    if not mgr or not mgr.current:
        return CommandResult(text="No active session to stash.", success=False)
    name = ctx.args.strip() or f"stash-{int(time.time())}"
    mgr.current.name = f"[stash] {name}"
    mgr.save_current()
    mgr.new()
    return CommandResult(text=f"Stashed session as: {name}\nNew session started.")


@command("pop", description="Pop the most recently stashed session",
         usage="/pop", category="session", permission=PermLevel.STANDARD)
async def cmd_pop(ctx: CommandContext) -> CommandResult:
    mgr = ctx.session_mgr
    if not mgr:
        return CommandResult(text="Session manager not available.", success=False)
    sessions = mgr.list_sessions(limit=20)
    stashed = [s for s in sessions if s.get("name", "").startswith("[stash]")]
    if not stashed:
        return CommandResult(text="No stashed sessions.", success=False)
    latest = stashed[0]
    session = mgr.find(latest["id"])
    if session:
        mgr.save_current()
        mgr.resume(session)
        session.name = session.name.replace("[stash] ", "")
        return CommandResult(text=f"Popped stashed session: {session.display_name}")
    return CommandResult(text="Failed to pop stash.", success=False)


@command("whoami", description="Show current user and system info",
         usage="/whoami", category="core", permission=PermLevel.READ_ONLY)
async def cmd_whoami(ctx: CommandContext) -> CommandResult:
    import os
    import platform
    user = os.environ.get("USER", os.environ.get("USERNAME", "unknown"))
    host = platform.node()
    shell = os.environ.get("SHELL", "unknown")
    cwd = os.getcwd()
    lines = [
        f"User:     {user}",
        f"Host:     {host}",
        f"Shell:    {shell}",
        f"CWD:      {cwd}",
        f"Platform: {platform.system()} {platform.release()}",
    ]
    return CommandResult(text="\n".join(lines))


@command("version", description="Show JARVIS version and build info",
         usage="/version", category="core", permission=PermLevel.READ_ONLY)
async def cmd_version(ctx: CommandContext) -> CommandResult:
    lines = [
        "JARVIS v2.0",
        "  Autonomous AI agent CLI",
        "  Built by Ulrich",
        "  Python-based \u2022 LLM-independent architecture",
    ]
    return CommandResult(text="\n".join(lines))


@command("uptime", description="Show session uptime and stats",
         usage="/uptime", category="core", permission=PermLevel.READ_ONLY)
async def cmd_uptime(ctx: CommandContext) -> CommandResult:
    mgr = ctx.session_mgr
    if mgr and mgr.current:
        s = mgr.current
        created = s.created_at if hasattr(s, 'created_at') else None
        if created:
            elapsed = time.time() - created
            hours = int(elapsed // 3600)
            minutes = int((elapsed % 3600) // 60)
            return CommandResult(
                text=f"Session: {s.display_name}\n"
                     f"  Uptime: {hours}h {minutes}m\n"
                     f"  Turns:  {s.turn_count}"
            )
    return CommandResult(text="No active session.", success=False)


@command("alias", description="Create a command alias",
         usage="/alias <name> <command>", category="core", permission=PermLevel.STANDARD)
async def cmd_alias(ctx: CommandContext) -> CommandResult:
    parts = ctx.args.strip().split(None, 1)
    if len(parts) < 2:
        return CommandResult(text="Usage: /alias <name> <command>", success=False)
    name, target = parts
    return CommandResult(
        text=f"Alias support coming soon.\n"
             f"  Would map /{name} -> {target}"
    )


@command("diff", description="Show diff of recent file changes",
         usage="/diff [file]", category="git", permission=PermLevel.READ_ONLY)
async def cmd_diff(ctx: CommandContext) -> CommandResult:
    import subprocess
    target = ctx.args.strip()
    cmd = ["git", "diff", "--stat"]
    if target:
        cmd.append(target)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        output = result.stdout.strip()
        if not output:
            return CommandResult(text="No changes detected.")
        return CommandResult(text=output)
    except FileNotFoundError:
        return CommandResult(text="git not found.", success=False)
    except subprocess.TimeoutExpired:
        return CommandResult(text="git diff timed out.", success=False)


@command("compact", description="Compact conversation history to save context",
         usage="/compact [topic]", category="session", permission=PermLevel.STANDARD)
async def cmd_compact(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available", success=False)
    topic = ctx.args.strip()
    history = brain.memory.get_history(limit=100)
    before = len(history)
    # Summarize and compress
    if hasattr(brain.memory, 'compact'):
        summary = brain.memory.compact(topic=topic)
        after = len(brain.memory.get_history(limit=100))
        return CommandResult(
            text=f"Compacted {before} turns -> {after} turns.\n"
                 f"Summary preserved in memory."
        )
    return CommandResult(text="Memory compaction not yet implemented for this backend.")


@command("review", description="Review recent changes or code",
         usage="/review [file|commit]", category="git", permission=PermLevel.READ_ONLY)
async def cmd_review(ctx: CommandContext) -> CommandResult:
    import subprocess
    target = ctx.args.strip()
    if target:
        cmd = ["git", "diff", target]
    else:
        cmd = ["git", "diff", "HEAD~1"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        diff = result.stdout.strip()
        if not diff:
            return CommandResult(text="No diff to review.")
        # Truncate if very large
        if len(diff) > 4000:
            diff = diff[:4000] + "\n\n... (truncated, use git diff directly for full output)"
        return CommandResult(text=f"Code Review\n{'=' * 40}\n{diff}")
    except FileNotFoundError:
        return CommandResult(text="git not found.", success=False)
    except subprocess.TimeoutExpired:
        return CommandResult(text="git diff timed out.", success=False)


@command("init", description="Initialize JARVIS in current directory",
         usage="/init", category="core", permission=PermLevel.STANDARD)
async def cmd_init(ctx: CommandContext) -> CommandResult:
    import os
    jarvis_dir = os.path.join(os.getcwd(), ".jarvis")
    if os.path.exists(jarvis_dir):
        return CommandResult(text=f"Already initialized: {jarvis_dir}")
    os.makedirs(jarvis_dir, exist_ok=True)
    # Create default config
    config_path = os.path.join(jarvis_dir, "config.json")
    import json
    config = {
        "project": os.path.basename(os.getcwd()),
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "settings": {},
    }
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)
    return CommandResult(
        text=f"Initialized JARVIS project in: {jarvis_dir}\n"
             f"  Created: {config_path}"
    )


@command("cost", description="Show estimated token cost for this session",
         usage="/cost", category="core", permission=PermLevel.READ_ONLY)
async def cmd_cost(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available", success=False)
    stats = {}
    if hasattr(brain, 'reasoner') and hasattr(brain.reasoner, 'usage_stats'):
        stats = brain.reasoner.usage_stats
    input_tokens = stats.get("input_tokens", 0)
    output_tokens = stats.get("output_tokens", 0)
    # Rough cost estimate (varies by model)
    cost = (input_tokens * 0.25 + output_tokens * 1.25) / 1_000_000
    lines = [
        "Session Cost Estimate",
        f"  Input tokens:  {input_tokens:,}",
        f"  Output tokens: {output_tokens:,}",
        f"  Est. cost:     ${cost:.4f}",
    ]
    return CommandResult(text="\n".join(lines))


@command("export", description="Export conversation to a file",
         usage="/export [format] [path]", category="session", permission=PermLevel.STANDARD)
async def cmd_export(ctx: CommandContext) -> CommandResult:
    import json as _json
    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available", success=False)
    parts = ctx.args.strip().split()
    fmt = parts[0] if parts else "json"
    path = parts[1] if len(parts) > 1 else f"jarvis-export-{int(time.time())}.{fmt}"
    history = brain.memory.get_history(limit=500)
    if fmt == "json":
        with open(path, "w") as f:
            _json.dump(history, f, indent=2, default=str)
    elif fmt == "md":
        with open(path, "w") as f:
            for h in history:
                role = h.get("role", "unknown")
                f.write(f"## {role}\n\n{h.get('content', '')}\n\n---\n\n")
    else:
        return CommandResult(text=f"Unknown format: {fmt}. Use 'json' or 'md'.", success=False)
    return CommandResult(text=f"Exported {len(history)} messages to: {path}")
