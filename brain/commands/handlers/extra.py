"""Extra commands — additional Claude Code-style commands for JARVIS."""
import os
import time
import subprocess
from brain.commands.registry import command, CommandContext, CommandResult, PermLevel


# ── Companion (/buddy) ──────────────────────────────────────────────

@command("buddy", description="Show or interact with your AI companion",
         usage="/buddy [pet|off|on|switch <name>]", category="core", permission=PermLevel.READ_ONLY)
async def cmd_buddy(ctx: CommandContext) -> CommandResult:
    from shells.cli.companion import Companion, COMPANIONS
    args = ctx.args.strip().lower()

    # Get or create companion on brain
    brain = ctx.brain
    if brain and not hasattr(brain, '_companion'):
        brain._companion = Companion()

    companion = brain._companion if brain else Companion()

    if args == "pet":
        comment = companion.get_comment("pet")
        return CommandResult(text=companion.render_comment(comment))
    elif args == "off":
        companion.enabled = False
        return CommandResult(text=f"{companion.name} goes quiet. (/buddy on to bring back)")
    elif args == "on":
        companion.enabled = True
        return CommandResult(text=f"{companion.name} is back. Watching.")
    elif args.startswith("switch"):
        name = args.replace("switch", "").strip()
        if name in COMPANIONS:
            if brain:
                brain._companion = Companion(name)
            return CommandResult(text=Companion(name).render_card())
        available = ", ".join(COMPANIONS.keys())
        return CommandResult(text=f"Unknown companion. Available: {available}")
    elif args == "rename":
        return CommandResult(text=f"{companion.name} doesn't want a new name. Deal with it.")
    else:
        # Show companion card with interactive footer
        card = companion.render_card()
        footer = (
            f"\n{companion.name} is here \u00b7 it'll chime in as you code\n"
            f"your buddy won't count toward your usage\n"
            f"say its name to get its take \u00b7 /buddy pet \u00b7 /buddy off\n"
            f"\npress any key"
        )
        return CommandResult(text=card + footer)


@command("reload", description="Hot-reload JARVIS modules without restarting",
         usage="/reload [module]", category="core", permission=PermLevel.STANDARD)
async def cmd_reload(ctx: CommandContext) -> CommandResult:
    import importlib
    target = ctx.args.strip()

    if target:
        # Reload specific module
        try:
            mod = importlib.import_module(target)
            importlib.reload(mod)
            return CommandResult(text=f"Reloaded: {target}")
        except Exception as e:
            return CommandResult(text=f"Failed to reload {target}: {e}", success=False)

    # Reload all brain modules
    import sys
    reloaded = 0
    errors = []
    brain_modules = sorted([name for name in sys.modules if name.startswith("brain.")])
    for name in brain_modules:
        try:
            mod = sys.modules[name]
            if hasattr(mod, '__file__') and mod.__file__:
                importlib.reload(mod)
                reloaded += 1
        except Exception as e:
            errors.append(f"{name}: {e}")

    # Re-discover plugins and skills
    brain = ctx.brain
    if brain:
        brain.plugins.discover()
        brain.skills.discover()

    lines = [f"Hot-reloaded {reloaded} brain modules."]
    if brain:
        lines.append(f"  Plugins: {len(brain.plugins.list_plugins())}")
        lines.append(f"  Skills: {len(brain.skills.list_skills())}")
    if errors:
        lines.append(f"\n  {len(errors)} errors:")
        for e in errors[:5]:
            lines.append(f"    {e}")
    return CommandResult(text="\n".join(lines))


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


def _load_billing():
    """Load persistent billing data."""
    import json
    billing_path = os.path.expanduser("~/.jarvis/billing.json")
    if os.path.exists(billing_path):
        try:
            return json.loads(open(billing_path).read())
        except Exception:
            pass
    return {"total_credit": 20.00, "total_used": 0, "remaining": 20.00}


def _save_billing(data):
    """Save billing data."""
    import json
    billing_path = os.path.expanduser("~/.jarvis/billing.json")
    os.makedirs(os.path.dirname(billing_path), exist_ok=True)
    open(billing_path, "w").write(json.dumps(data, indent=2))


@command("cost", description="Show API cost — this session + total account usage",
         usage="/cost", category="core", permission=PermLevel.READ_ONLY)
async def cmd_cost(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available", success=False)
    stats = brain.reasoner.usage_stats if hasattr(brain, 'reasoner') else {}
    inp = stats.get("input_tokens", 0)
    out = stats.get("output_tokens", 0)
    total = inp + out
    session_cost = stats.get("cost_usd", 0)
    calls = stats.get("calls", 0)
    model = stats.get("model", "unknown")

    # Load persistent billing
    billing = _load_billing()
    account_used = billing.get("total_used", 0)
    account_credit = billing.get("total_credit", 20.00)
    account_remaining = billing.get("remaining", account_credit - account_used)

    # Update billing with session cost
    total_used = account_used + session_cost
    remaining = account_credit - total_used
    pct = min(100, int(total_used / account_credit * 100)) if account_credit > 0 else 0
    bar_filled = pct // 5
    bar_empty = 20 - bar_filled
    bar = "\u2588" * bar_filled + "\u2591" * bar_empty

    lines = [
        "JARVIS Cost Dashboard",
        "=" * 40,
        "",
        "  This Session",
        "  " + "-" * 30,
        f"  Model:          {model}",
        f"  API calls:      {calls}",
        f"  Input tokens:   {inp:,}",
        f"  Output tokens:  {out:,}",
        f"  Session cost:   ${session_cost:.4f}",
        "",
        "  Account",
        "  " + "-" * 30,
        f"  Total credit:   ${account_credit:.2f}",
        f"  Total used:     ${total_used:.4f}",
        f"  Remaining:      ${remaining:.4f}",
        f"  Usage:          {bar} {pct}%",
    ]

    if remaining < 1.0:
        lines.append(f"  \u26a0 LOW BALANCE — consider adding credits")
    if remaining <= 0:
        lines.append(f"  \u26a0 BALANCE DEPLETED — JARVIS will stop making API calls")

    budget = getattr(brain, '_cost_budget', None)
    if budget:
        budget_remaining = budget - session_cost
        lines.append(f"")
        lines.append(f"  Session budget:  ${budget:.2f} (${budget_remaining:.4f} left)")

    return CommandResult(text="\n".join(lines))


@command("budget", description="Set session spending limit or update account balance",
         usage="/budget <amount> | /budget credit <total> | /budget used <amount>",
         category="core", permission=PermLevel.STANDARD)
async def cmd_budget(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available", success=False)
    args = ctx.args.strip().split()

    if not args:
        billing = _load_billing()
        budget = getattr(brain, '_cost_budget', None)
        lines = [f"Account: ${billing.get('remaining', 0):.2f} remaining of ${billing.get('total_credit', 0):.2f}"]
        if budget:
            lines.append(f"Session budget: ${budget:.2f}")
        lines.append("")
        lines.append("Usage:")
        lines.append("  /budget 5.00          Set session spending limit")
        lines.append("  /budget credit 20.00  Set total account credit")
        lines.append("  /budget used 2.44     Set total amount already used")
        lines.append("  /budget sync          Sync session cost to account")
        return CommandResult(text="\n".join(lines))

    if args[0] == "credit" and len(args) > 1:
        try:
            amount = float(args[1].replace("$", ""))
            billing = _load_billing()
            billing["total_credit"] = amount
            billing["remaining"] = amount - billing.get("total_used", 0)
            _save_billing(billing)
            return CommandResult(text=f"Account credit set to ${amount:.2f}")
        except ValueError:
            return CommandResult(text="Usage: /budget credit 20.00", success=False)

    if args[0] == "used" and len(args) > 1:
        try:
            amount = float(args[1].replace("$", ""))
            billing = _load_billing()
            billing["total_used"] = amount
            billing["remaining"] = billing.get("total_credit", 20.0) - amount
            import time as _t
            billing["last_updated"] = _t.strftime("%Y-%m-%d")
            _save_billing(billing)
            return CommandResult(text=f"Account usage set to ${amount:.2f}\nRemaining: ${billing['remaining']:.2f}")
        except ValueError:
            return CommandResult(text="Usage: /budget used 2.44", success=False)

    if args[0] == "sync":
        billing = _load_billing()
        session_cost = brain.reasoner.usage_stats.get("cost_usd", 0) if hasattr(brain, 'reasoner') else 0
        billing["total_used"] = billing.get("total_used", 0) + session_cost
        billing["remaining"] = billing.get("total_credit", 20.0) - billing["total_used"]
        import time as _t
        billing["last_updated"] = _t.strftime("%Y-%m-%d")
        _save_billing(billing)
        brain.reasoner.session_cost_usd = 0  # Reset session counter
        brain.reasoner.session_input_tokens = 0
        brain.reasoner.session_output_tokens = 0
        brain.reasoner.session_calls = 0
        return CommandResult(text=f"Synced. Total used: ${billing['total_used']:.4f}, Remaining: ${billing['remaining']:.4f}")

    # Default: set session budget
    try:
        amount = float(args[0].replace("$", ""))
        brain._cost_budget = amount
        return CommandResult(text=f"Session budget: ${amount:.2f}\nJARVIS stops when this session exceeds it.")
    except ValueError:
        return CommandResult(text="Usage: /budget 5.00", success=False)


# ── Claude Code-style commands ──────────────────────────────────────

@command("btw", description="Send an inline side note to the user during a task",
         usage="/btw <message>", category="core", permission=PermLevel.READ_ONLY)
async def cmd_btw(ctx: CommandContext) -> CommandResult:
    msg = ctx.args.strip()
    if not msg:
        return CommandResult(text="Usage: /btw <message>", success=False)
    return CommandResult(text=f"BTW: {msg}")


@command("effort", description="Set response effort level",
         usage="/effort [low|medium|high]", category="core", permission=PermLevel.STANDARD)
async def cmd_effort(ctx: CommandContext) -> CommandResult:
    level = ctx.args.strip().lower()
    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available", success=False)
    if level in ("low", "medium", "high"):
        brain._effort_level = level
        hints = {
            "low": "Quick answers, minimal detail. Good for simple questions.",
            "medium": "Balanced — default level. Thorough but not exhaustive.",
            "high": "Deep analysis, comprehensive answers. More tokens, more time.",
        }
        return CommandResult(text=f"Effort: {level}\n  {hints[level]}")
    current = getattr(brain, '_effort_level', 'medium')
    return CommandResult(text=f"Current effort: {current}\nUsage: /effort [low|medium|high]")


@command("statusline", description="Configure the bottom status bar",
         usage="/statusline [on|off|default]", category="core", permission=PermLevel.STANDARD)
async def cmd_statusline(ctx: CommandContext) -> CommandResult:
    arg = ctx.args.strip().lower()
    if arg == "off":
        return CommandResult(text="Status line disabled. (Restart to apply)")
    elif arg == "on" or arg == "default":
        return CommandResult(text="Status line enabled (default).")
    return CommandResult(
        text="Status Line Config\n"
             "  /statusline on      Show status bar\n"
             "  /statusline off     Hide status bar\n"
             "  /statusline default Reset to defaults\n\n"
             "Status bar shows: model · tokens · mode"
    )


@command("allowed-tools", description="Show which tools the agent can use",
         usage="/allowed-tools", category="core", permission=PermLevel.READ_ONLY)
async def cmd_allowed_tools(ctx: CommandContext) -> CommandResult:
    from brain.agent.tools import TOOL_SCHEMAS
    lines = ["Available Tools", "=" * 40]
    for tool in TOOL_SCHEMAS:
        func = tool.get("function", {})
        name = func.get("name", "?")
        desc = func.get("description", "")[:60]
        lines.append(f"  {name:<15s} {desc}")
    lines.append(f"\n  Total: {len(TOOL_SCHEMAS)} tools")
    brain = ctx.brain
    if brain:
        mcp_tools = brain.mcp.list_tools()
        if mcp_tools:
            lines.append(f"  MCP tools: {len(mcp_tools)}")
    return CommandResult(text="\n".join(lines))


@command("terminal-setup", description="Configure terminal for optimal JARVIS display",
         usage="/terminal-setup", category="core", permission=PermLevel.READ_ONLY)
async def cmd_terminal_setup(ctx: CommandContext) -> CommandResult:
    return CommandResult(
        text="Terminal Setup\n"
             "=" * 40 + "\n"
             "  Recommended: 120+ columns, 30+ rows\n"
             "  Font: Any monospace (Fira Code, JetBrains Mono)\n"
             "  Theme: Dark background\n"
             "  Unicode: Required (UTF-8)\n\n"
             "  Your terminal:\n"
             f"    Size: {os.get_terminal_size().columns}x{os.get_terminal_size().lines}\n"
             f"    TERM: {os.environ.get('TERM', 'unknown')}\n"
             f"    LANG: {os.environ.get('LANG', 'unknown')}"
    )


@command("intro", description="Show the welcome screen again",
         usage="/intro", category="core", permission=PermLevel.READ_ONLY)
async def cmd_intro(ctx: CommandContext) -> CommandResult:
    return CommandResult(
        text="  \033[36m╔═▓▓▓▓═╗\033[0m   \033[1mJARVIS v2.0\033[0m\n"
             "  \033[36m║ \033[1mJ.A.R.V.I.S\033[0m\033[36m ║\033[0m  Autonomous AI Agent\n"
             "  \033[36m╚═▓▓▓▓═╝\033[0m   Built by Ulrich\n\n"
             "  /help         All commands\n"
             "  /doctor       Check health\n"
             "  /status       Current state\n"
             "  /model        Switch AI model\n"
             "  /effort       Set response depth\n"
             "  ?             Keyboard shortcuts"
    )


@command("new", aliases=["reset"], description="Start a fresh conversation",
         usage="/new", category="session", permission=PermLevel.STANDARD)
async def cmd_new(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    mgr = ctx.session_mgr
    if mgr:
        mgr.save_current()
        mgr.new()
    if brain:
        # Clear conversation memory for fresh start
        try:
            import sqlite3
            from brain.config import DATA_DIR
            db_path = DATA_DIR / "jarvis.db"
            if db_path.exists():
                conn = sqlite3.connect(str(db_path))
                conn.execute("DELETE FROM conversations")
                conn.commit()
                conn.close()
        except Exception:
            pass
    return CommandResult(text="Fresh conversation started.", action="clear")


@command("rewind", description="Undo the last exchange (remove last user+assistant turn)",
         usage="/rewind [N]", category="session", permission=PermLevel.STANDARD)
async def cmd_rewind(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available", success=False)
    n = 1
    if ctx.args.strip().isdigit():
        n = int(ctx.args.strip())
    try:
        import sqlite3
        from brain.config import DATA_DIR
        db_path = DATA_DIR / "jarvis.db"
        if db_path.exists():
            conn = sqlite3.connect(str(db_path))
            # Delete last N*2 rows (user + jarvis pairs)
            count = n * 2
            conn.execute(f"DELETE FROM conversations WHERE id IN "
                         f"(SELECT id FROM conversations ORDER BY id DESC LIMIT {count})")
            conn.commit()
            conn.close()
            return CommandResult(text=f"Rewound {n} exchange{'s' if n > 1 else ''}.")
    except Exception as e:
        return CommandResult(text=f"Rewind failed: {e}", success=False)
    return CommandResult(text="Nothing to rewind.")


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
