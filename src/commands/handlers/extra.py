"""Extra commands — additional Extended commands for JARVIS."""
import os
import re
import json
import time
import asyncio
import subprocess
from pathlib import Path
from src.commands.registry import command, CommandContext, CommandResult, PermLevel


# ── Companion (/buddy) ──────────────────────────────────────────────

@command("buddy", description="Show or interact with your AI companion",
         usage="/buddy [pet|off|on|switch <name>]", category="core", permission=PermLevel.READ_ONLY)
async def cmd_buddy(ctx: CommandContext) -> CommandResult:
    from src.cli.companion import Companion, COMPANIONS
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
    brain_modules = sorted([name for name in sys.modules if name.startswith("src.")])
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


@command("launch-desktop", aliases=["start-desktop"],
         description="Launch JARVIS desktop overlay (GTK+WebKit)",
         usage="/launch-desktop", category="core", permission=PermLevel.STANDARD)
async def cmd_desktop(ctx: CommandContext) -> CommandResult:
    from src.commands.desktop.desktop import _is_desktop_running

    if _is_desktop_running():
        return CommandResult(text="JARVIS desktop is already running.")

    jarvis_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    log_path = "/tmp/jarvis-desktop.log"

    with open(log_path, "w") as log_file:
        tauri_bin = os.path.join(jarvis_root, "src", "desktop-tauri", "src-tauri", "target", "debug", "jarvis-desktop")
        subprocess.Popen(
            [tauri_bin],
            cwd=jarvis_root,
            start_new_session=True,
            stdout=log_file,
            stderr=log_file,
            env={**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0.0")},
        )
    return CommandResult(text=f"JARVIS desktop launching... (log: {log_path})")


# ---------------------------------------------------------------------------
# /switch -- Switch JARVIS between desktop / browser / CLI
# ---------------------------------------------------------------------------

@command("switch", aliases=["sw"], description="Switch JARVIS UI: desktop, browser, or status",
         usage="/switch <desktop|browser|status>", category="core", permission=PermLevel.STANDARD)
async def cmd_switch(ctx: CommandContext) -> CommandResult:
    target = ctx.args.strip().lower()

    if not target or target == "status":
        try:
            import urllib.request, json as _json
            resp = urllib.request.urlopen("http://127.0.0.1:8765/api/client/status", timeout=2)
            data = _json.loads(resp.read())
            desktop = "active" if data.get("desktop") else "inactive"
            browser = "active" if data.get("browser") else "inactive"
            return CommandResult(text=f"Desktop: {desktop}\nBrowser: {browser}")
        except Exception:
            return CommandResult(text="Server not running. Start with: jarvis-web or jarvis-desktop")

    if target in ("desktop", "d"):
        try:
            import urllib.request, json as _json
            data = _json.dumps({"target": "desktop"}).encode()
            req = urllib.request.Request(
                "http://127.0.0.1:8765/api/client/handoff",
                data=data, headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=2)
            return CommandResult(text="Switching to desktop.")
        except Exception:
            return CommandResult(text="Server not running. Start desktop with: jarvis-desktop", success=False)

    if target in ("browser", "b", "web"):
        try:
            env = {**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0.0")}
            subprocess.Popen(["xdg-open", "http://127.0.0.1:8765/"],
                             start_new_session=True, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, env=env)
            return CommandResult(text="Opening browser.")
        except Exception:
            return CommandResult(text="Failed to open browser.", success=False)

    return CommandResult(text="Usage: /switch <desktop|browser|status>", success=False)


# ---------------------------------------------------------------------------
# /color -- Change JARVIS theme color globally
# ---------------------------------------------------------------------------

@command("desktop-theme", description="Change JARVIS desktop theme color",
         usage="/desktop-theme <preset|#hex>  (presets: arc-reactor, iron-man, ultron, stealth, emerald, frost, solar, hotrod, ghost)",
         category="core", permission=PermLevel.STANDARD)
async def cmd_color(ctx: CommandContext) -> CommandResult:
    from src.desktop.colors import PRESETS, get_theme, get_colors, set_theme, set_custom_color, generate_icon

    args = ctx.args.strip().lower()
    if not args or args in ("list", "help"):
        current = get_theme()
        primary, glow = get_colors()
        lines = [f"Current theme: {current} ({primary})", "", "Available presets:"]
        for pid, (phex, _, label) in PRESETS.items():
            marker = " \u2022" if pid == current else "  "
            lines.append(f"{marker} {pid:14s}  {phex}  {label}")
        lines.append("")
        lines.append("Usage: /color <preset>  or  /color #hex")
        return CommandResult(text="\n".join(lines))

    if args.startswith("#") and len(args) in (4, 7):
        if len(args) == 4:
            args = f"#{args[1]*2}{args[2]*2}{args[3]*2}"
        primary, glow = set_custom_color(args)
        generate_icon(primary)
        return CommandResult(text=f"Theme set to custom color: {primary} (glow: {glow})")

    if args in PRESETS:
        primary, glow = set_theme(args)
        generate_icon(primary)
        _, _, label = PRESETS[args]
        return CommandResult(text=f"Theme set to {label}: {primary}")

    return CommandResult(text=f"Unknown theme '{args}'. Use /color list to see options.", success=False)


# NOTE: /add-dir command is in remote.py (uses state manager)


# NOTE: /context command moved to core.py with enhanced breakdown display


def _extract_code_blocks(text: str) -> list[str]:
    """Extract fenced code blocks from markdown text."""
    pattern = r'```(?:\w+)?\s*\n(.*?)```'
    blocks = re.findall(pattern, text, re.DOTALL)
    return [b.strip() for b in blocks if b.strip()]


def _copy_to_clipboard(content: str) -> tuple[bool, str]:
    """Copy text to clipboard. Returns (success, method_used)."""
    # Try xclip first (Linux)
    for cmd, args in [
        ("xclip", ["xclip", "-selection", "clipboard"]),
        ("xsel", ["xsel", "--clipboard", "--input"]),
        ("pbcopy", ["pbcopy"]),
        ("wl-copy", ["wl-copy"]),
    ]:
        try:
            subprocess.run(args, input=content.encode(), check=True, timeout=5,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True, cmd
        except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            continue
    # Fallback: write to temp file
    fallback = "/tmp/jarvis-clipboard.txt"
    try:
        with open(fallback, "w") as f:
            f.write(content)
        return True, f"file ({fallback})"
    except Exception:
        return False, "none"


@command("copy", description="Copy last response to clipboard (or /copy N for the Nth-latest)",
         usage="/copy [N] [--code]", category="core", permission=PermLevel.READ_ONLY)
async def cmd_copy(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available", success=False)

    args = ctx.args.strip()
    want_code = "--code" in args
    args = args.replace("--code", "").strip()
    n = int(args) if args.isdigit() else 1
    lookback = max(n, 5)

    history = brain.memory.get_history(limit=50)
    jarvis_msgs = [h for h in history if h["role"] == "jarvis"]
    if not jarvis_msgs:
        return CommandResult(text="No response to copy.", success=False)

    # Smart code block extraction: scan last N assistant messages
    if want_code or True:  # always try code blocks first
        code_blocks = []
        scan_count = min(lookback, len(jarvis_msgs))
        for msg in jarvis_msgs[-scan_count:]:
            blocks = _extract_code_blocks(msg.get("content", ""))
            code_blocks.extend(blocks)

        if code_blocks:
            if n <= len(code_blocks):
                # Copy the Nth most recent code block
                content = code_blocks[-n]
                label = f"code block {n} of {len(code_blocks)}"
            else:
                # Copy all code blocks joined
                content = "\n\n".join(code_blocks)
                label = f"all {len(code_blocks)} code blocks"

            ok, method = _copy_to_clipboard(content)
            if ok:
                preview = content[:80].replace('\n', ' ')
                if len(content) > 80:
                    preview += "..."
                return CommandResult(
                    text=f"Copied {label} ({len(content)} chars) via {method}\n"
                         f"  Preview: {preview}"
                )
            return CommandResult(text="Failed to copy to clipboard.", success=False)

    # No code blocks found: copy full response
    if n > len(jarvis_msgs):
        return CommandResult(text=f"Only {len(jarvis_msgs)} responses available.", success=False)
    content = jarvis_msgs[-n]["content"]
    ok, method = _copy_to_clipboard(content)
    if ok:
        return CommandResult(text=f"Copied full response ({len(content)} chars) via {method}")
    return CommandResult(text="Failed to copy to clipboard.", success=False)


@command("doctor", description="Full diagnostic report of JARVIS installation",
         usage="/doctor", category="core", permission=PermLevel.READ_ONLY)
async def cmd_doctor(ctx: CommandContext) -> CommandResult:
    import shutil
    import platform
    import sys
    import psutil  # soft dependency

    lines = ["JARVIS Doctor", "=" * 44]
    issues = 0

    # -- Python --
    py_ver = sys.version.split()[0]
    py_ok = sys.version_info >= (3, 10)
    py_status = '\u2714' if py_ok else '\u2718 (3.10+ required)'
    lines.append(f"  Python:       {py_ver} {py_status}")
    if not py_ok:
        issues += 1
    lines.append(f"  Platform:     {platform.platform()}")

    # -- API keys --
    lines.append("")
    lines.append("  API Keys")
    lines.append("  " + "-" * 34)
    env_file = os.path.expanduser("~/.jarvis/.env")
    env_exists = os.path.exists(env_file)
    env_status = '\u2714' if env_exists else '\u2718 (~/.jarvis/.env)'
    lines.append(f"  .env file:    {env_status}")
    if not env_exists:
        issues += 1
    for name, label, required in [
        ("GROQ_API_KEY", "Groq", True),
        ("ANTHROPIC_API_KEY", "Anthropic", False),
        ("OPENAI_API_KEY", "OpenAI", False),
        ("XAI_API_KEY", "xAI/Grok", False),
    ]:
        present = bool(os.environ.get(name))
        if required and not present:
            issues += 1
        icon = '\u2714' if present else ('\u2718' if required else '\u2014')
        suffix = " (required)" if required and not present else ""
        lines.append(f"  {label:<12s}   {icon}{suffix}")

    # -- Providers --
    lines.append("")
    lines.append("  Providers")
    lines.append("  " + "-" * 34)
    providers_path = os.path.expanduser("~/.jarvis/providers.json")
    if os.path.exists(providers_path):
        try:
            pdata = json.loads(Path(providers_path).read_text())
            pcount = len(pdata) if isinstance(pdata, list) else len(pdata.get("providers", pdata))
            lines.append(f"  Config:       \u2714 ({pcount} providers)")
        except Exception:
            lines.append(f"  Config:       \u26a0 (parse error)")
            issues += 1
    else:
        lines.append(f"  Config:       \u2718 (no providers.json)")
        issues += 1

    # -- Ollama --
    try:
        import urllib.request
        resp = urllib.request.urlopen("http://localhost:11434/api/tags", timeout=2)
        data = json.loads(resp.read())
        model_count = len(data.get("models", []))
        lines.append(f"  Ollama:       \u2714 (running, {model_count} models)")
    except Exception:
        lines.append("  Ollama:       \u2718 (not running)")

    # -- MCP servers --
    lines.append("")
    lines.append("  MCP Servers")
    lines.append("  " + "-" * 34)
    mcp_path = os.path.expanduser("~/.jarvis/mcp.json")
    if os.path.exists(mcp_path):
        try:
            mcp_data = json.loads(Path(mcp_path).read_text())
            servers = mcp_data if isinstance(mcp_data, dict) else {}
            for srv_name in list(servers.keys())[:8]:
                lines.append(f"    {srv_name}")
            lines.append(f"  Total:        {len(servers)} servers configured")
        except Exception:
            lines.append(f"  Config:       \u26a0 (parse error)")
    else:
        lines.append(f"  Config:       \u2014 (no mcp.json)")

    # -- System tools --
    lines.append("")
    lines.append("  System Tools")
    lines.append("  " + "-" * 34)
    for tool in ["git", "nmap", "xclip", "xsel", "jq", "cargo", "node", "npm"]:
        path = shutil.which(tool)
        tool_icon = '\u2714' if path else '\u2718'
        lines.append(f"  {tool:<12s}   {tool_icon}")

    # -- System resources --
    lines.append("")
    lines.append("  System Resources")
    lines.append("  " + "-" * 34)
    try:
        disk = shutil.disk_usage("/")
        disk_free_gb = disk.free / (1024 ** 3)
        disk_icon = '\u2714' if disk_free_gb > 1.0 else '\u26a0'
        lines.append(f"  Disk free:    {disk_free_gb:.1f} GB {disk_icon}")
        if disk_free_gb < 1.0:
            issues += 1
    except Exception:
        pass
    try:
        mem = psutil.virtual_memory()
        mem_free_gb = mem.available / (1024 ** 3)
        mem_icon = '\u2714' if mem_free_gb > 0.5 else '\u26a0'
        lines.append(f"  RAM free:     {mem_free_gb:.1f} GB {mem_icon}")
        lines.append(f"  RAM used:     {mem.percent}%")
        if mem_free_gb < 0.5:
            issues += 1
    except ImportError:
        lines.append("  RAM:          (install psutil for memory info)")
    except Exception:
        pass

    # -- Git status --
    lines.append("")
    lines.append("  Git Status")
    lines.append("  " + "-" * 34)
    try:
        result = subprocess.run(["git", "status", "--porcelain", "-u"],
                                capture_output=True, text=True, timeout=5)
        changed = len([l for l in result.stdout.strip().split('\n') if l.strip()])
        branch = subprocess.run(["git", "branch", "--show-current"],
                                capture_output=True, text=True, timeout=5).stdout.strip()
        lines.append(f"  Branch:       {branch}")
        lines.append(f"  Changes:      {changed} file{'s' if changed != 1 else ''}")
    except Exception:
        lines.append("  Git:          not available")

    # -- Brain modules --
    brain = ctx.brain
    if brain:
        lines.append("")
        lines.append("  Brain")
        lines.append("  " + "-" * 34)
        lines.append(f"  Commands:     {brain.command_registry.visible_count}")
        lines.append(f"  Plugins:      {len(brain.plugins.list_plugins())}")
        lines.append(f"  Skills:       {len(brain.skills.list_skills())}")
        lines.append(f"  MCP tools:    {len(brain.mcp.list_tools())}")
        lines.append(f"  Memory nodes: {brain.memory.stats.get('lattice_nodes', 0)}")

    # -- Summary --
    lines.append("")
    lines.append("=" * 44)
    if issues == 0:
        lines.append("  All checks passed. JARVIS is healthy.")
    else:
        lines.append(f"  {issues} issue{'s' if issues != 1 else ''} found. Review above.")

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


# NOTE: /login and /logout commands are in remote.py


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
         usage="/fast [on|off]", category="core", permission=PermLevel.READ_ONLY)
async def cmd_fast(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available", success=False)

    arg = ctx.args.strip().lower()
    if arg == "on":
        brain._fast_mode = True
    elif arg == "off":
        brain._fast_mode = False
    else:
        brain._fast_mode = not getattr(brain, '_fast_mode', False)

    is_fast = brain._fast_mode

    # Persist preference
    settings_path = os.path.expanduser("~/.jarvis/settings.json")
    try:
        settings = json.loads(open(settings_path).read()) if os.path.exists(settings_path) else {}
        settings["fast_mode"] = is_fast
        os.makedirs(os.path.dirname(settings_path), exist_ok=True)
        with open(settings_path, "w") as f:
            json.dump(settings, f, indent=2)
    except Exception:
        pass

    # Show which model is used in each mode
    normal_model = getattr(brain.reasoner, 'active_model_name', 'default')
    fast_model = "unknown"
    if hasattr(brain.reasoner, 'get_fast_model'):
        fast_model = brain.reasoner.get_fast_model() or "query_fast provider"
    elif hasattr(brain, '_providers'):
        # Check for fast provider
        fast_model = "fastest available"

    lines = [
        f"Fast mode: {'ON' if is_fast else 'OFF'}",
        "",
        f"  Normal model:  {normal_model}",
        f"  Fast model:    {fast_model}",
        "",
        f"  Fast mode uses shorter prompts, smaller models, and skips",
        f"  deep reasoning. Good for quick questions and simple tasks.",
    ]
    return CommandResult(text="\n".join(lines))


@command("theme", description="Switch color theme (dark/light/auto)",
         usage="/theme [dark|light|auto]", category="core")
async def cmd_theme(ctx: CommandContext) -> CommandResult:
    """Set terminal color theme."""
    from src.config import JARVIS_HOME

    args = ctx.args.strip().lower() if ctx.args else ""
    valid_themes = ["dark", "light", "auto"]

    if not args or args == "status":
        try:
            settings_path = JARVIS_HOME / "settings.json"
            if settings_path.exists():
                settings = json.loads(settings_path.read_text())
                current = settings.get("theme", "dark")
            else:
                current = "dark"
        except Exception:
            current = "dark"
        return CommandResult(text=f"Current theme: {current}\nAvailable: {', '.join(valid_themes)}")

    if args not in valid_themes:
        return CommandResult(text=f"Unknown theme: {args}\nAvailable: {', '.join(valid_themes)}")

    try:
        settings_path = JARVIS_HOME / "settings.json"
        settings = {}
        if settings_path.exists():
            settings = json.loads(settings_path.read_text())
        settings["theme"] = args
        settings_path.write_text(json.dumps(settings, indent=2))
        # Apply theme in real-time to the running CLI
        try:
            from src.cli.jarvis_cli import _apply_theme
            _apply_theme(args)
        except Exception:
            pass  # Not running in CLI context
        return CommandResult(text=f"Theme set to: {args}\nColors updated — takes effect immediately.")
    except Exception as e:
        return CommandResult(text=f"Error saving theme: {e}", success=False)


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


@command("feedback", description="Submit feedback (stored locally)",
         usage="/feedback <your message>", category="core", permission=PermLevel.READ_ONLY)
async def cmd_feedback(ctx: CommandContext) -> CommandResult:
    msg = ctx.args.strip()
    if not msg:
        return CommandResult(
            text="JARVIS Feedback\n"
                 "  Usage: /feedback <your message>\n\n"
                 "  Feedback is stored in ~/.jarvis/feedback.jsonl\n"
                 "  GitHub: https://github.com/ulrich/jarvis"
        )
    # Store feedback persistently
    feedback_path = os.path.expanduser("~/.jarvis/feedback.jsonl")
    os.makedirs(os.path.dirname(feedback_path), exist_ok=True)
    entry = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "message": msg,
        "cwd": os.getcwd(),
        "session": None,
    }
    brain = ctx.brain
    if brain:
        mgr = ctx.session_mgr
        if mgr and mgr.current:
            entry["session"] = mgr.current.display_name
        entry["model"] = getattr(brain.reasoner, 'active_model_name', '')
    try:
        with open(feedback_path, "a") as f:
            f.write(json.dumps(entry) + "\n")
        # Count total feedback entries
        with open(feedback_path, "r") as f:
            count = sum(1 for _ in f)
        return CommandResult(
            text=f"Feedback recorded. Thank you.\n"
                 f"  Stored in: {feedback_path}\n"
                 f"  Total entries: {count}"
        )
    except Exception as e:
        return CommandResult(text=f"Failed to store feedback: {e}", success=False)


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


# NOTE: /version command moved to core.py with model, provider, and context window info


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


def _colorize_diff(diff_text: str) -> str:
    """Add ANSI colors to diff output: green for +, red for -, cyan for @@."""
    lines = []
    for line in diff_text.split('\n'):
        if line.startswith('+') and not line.startswith('+++'):
            lines.append(f"\033[32m{line}\033[0m")  # green
        elif line.startswith('-') and not line.startswith('---'):
            lines.append(f"\033[31m{line}\033[0m")  # red
        elif line.startswith('@@'):
            lines.append(f"\033[36m{line}\033[0m")  # cyan
        elif line.startswith('diff ') or line.startswith('index '):
            lines.append(f"\033[1m{line}\033[0m")   # bold
        else:
            lines.append(line)
    return '\n'.join(lines)


# NOTE: /diff, /review, /init commands are in git.py (richer implementations)


# NOTE: /cost command moved to core.py with per-model breakdown and cache token counts


# ── Billing helpers ────────────────────────────────────────────────

def _load_billing() -> dict:
    """Load billing data from ~/.jarvis/billing.json."""
    try:
        from src.config import JARVIS_HOME
        billing_path = JARVIS_HOME / "billing.json"
        if billing_path.exists():
            return json.loads(billing_path.read_text())
    except Exception:
        pass
    return {"total_credit": 20.0, "total_used": 0.0, "remaining": 20.0}


def _save_billing(data: dict):
    """Save billing data to ~/.jarvis/billing.json."""
    try:
        from src.config import JARVIS_HOME
        billing_path = JARVIS_HOME / "billing.json"
        billing_path.parent.mkdir(parents=True, exist_ok=True)
        billing_path.write_text(json.dumps(data, indent=2) + "\n")
    except Exception:
        pass


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


# ── Extended commands ──────────────────────────────────────

@command("btw", description="Ask a side question without interrupting main conversation",
         usage="/btw <question>", category="core", permission=PermLevel.READ_ONLY)
async def cmd_btw(ctx: CommandContext) -> CommandResult:
    msg = ctx.args.strip()
    if not msg:
        return CommandResult(text="Usage: /btw <question>\n  Asks a side question without polluting conversation history.", success=False)

    brain = ctx.brain
    if not brain:
        return CommandResult(text=f"BTW: {msg}")

    # Run the side query asynchronously without adding to main conversation history
    try:
        _system = "You are answering a quick side question. Be brief and direct."
        reasoner = getattr(brain, 'reasoner', None)
        if reasoner and hasattr(reasoner, 'query'):
            import inspect
            sig = inspect.signature(reasoner.query)
            params = list(sig.parameters)
            # GroqReasoner.query(user_input, system_prompt, history=None)
            if len(params) >= 3 and params[1] in ('system_prompt', 'system'):
                response = await reasoner.query(msg, _system)
            # OpenAI-style query(messages: list)
            elif params[1] == 'messages':
                response = await reasoner.query([
                    {"role": "system", "content": _system},
                    {"role": "user", "content": msg},
                ])
            else:
                response = await brain.think(msg)
        else:
            # Fallback: just use brain.think but mark as side query
            response = await brain.think(msg)

        # Format as a visually distinct side-note
        separator = "\u2500" * 40
        result_text = (
            f"\033[2m{separator}\033[0m\n"
            f"\033[1m[BTW]\033[0m {msg}\n\n"
            f"{response}\n"
            f"\033[2m{separator}\033[0m"
        )
        # Return without adding to history (CommandResult doesn't auto-store)
        return CommandResult(text=result_text)
    except Exception as e:
        return CommandResult(text=f"BTW query failed: {e}\n\nOriginal question: {msg}", success=False)


@command("effort", description="Set response effort level",
         usage="/effort [low|medium|high|max]", category="core", permission=PermLevel.STANDARD)
async def cmd_effort(ctx: CommandContext) -> CommandResult:
    level = ctx.args.strip().lower()
    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available", success=False)

    effort_info = {
        "low": {
            "desc": "Quick answers, minimal detail",
            "behavior": "Short responses, no exploration, skips examples. Best for yes/no questions, simple lookups, quick commands.",
            "tokens": "~100-500 output tokens",
            "thinking": "Minimal",
        },
        "medium": {
            "desc": "Balanced (default)",
            "behavior": "Thorough but not exhaustive. Explains reasoning, includes relevant examples. Good for most tasks.",
            "tokens": "~500-2000 output tokens",
            "thinking": "Standard",
        },
        "high": {
            "desc": "Deep analysis, comprehensive answers",
            "behavior": "Explores edge cases, provides alternatives, includes code examples and references. Good for complex problems.",
            "tokens": "~2000-4000 output tokens",
            "thinking": "Extended",
        },
        "max": {
            "desc": "Exhaustive, leave no stone unturned",
            "behavior": "Maximum depth analysis, full exploration of options, detailed step-by-step. Research-grade thoroughness. Slow and expensive.",
            "tokens": "~4000+ output tokens",
            "thinking": "Maximum budget",
        },
    }

    if level in effort_info:
        brain._effort_level = level
        # Push to provider registry immediately
        try:
            brain.reasoner.providers.set_effort(level)
        except Exception:
            pass
        # Persist preference
        settings_path = os.path.expanduser("~/.jarvis/settings.json")
        try:
            settings = json.loads(open(settings_path).read()) if os.path.exists(settings_path) else {}
            settings["effort_level"] = level
            os.makedirs(os.path.dirname(settings_path), exist_ok=True)
            with open(settings_path, "w") as f:
                json.dump(settings, f, indent=2)
        except Exception:
            pass
        info = effort_info[level]
        return CommandResult(
            text=f"Effort: {level} -- {info['desc']}\n"
                 f"  {info['behavior']}\n"
                 f"  Typical output: {info['tokens']}\n"
                 f"  Thinking: {info['thinking']}"
        )

    current = getattr(brain, '_effort_level', 'medium')
    lines = [
        f"Current effort: {current}",
        "",
        "Available levels:",
    ]
    for lvl, info in effort_info.items():
        marker = " <-- current" if lvl == current else ""
        lines.append(f"  {lvl:<8s} {info['desc']}{marker}")
        lines.append(f"           {info['behavior'][:70]}")
    lines.append(f"\nUsage: /effort <level>")
    return CommandResult(text="\n".join(lines))


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
    from src.agent.tools import TOOL_SCHEMAS
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


# NOTE: /terminal-setup command is in remote.py


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
            from src.config import DATA_DIR
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
        from src.config import DATA_DIR
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


# NOTE: /export command is in session.py (supports md, JSON, txt)


# ── New commands (Extended) ────────────────────────────


def _load_settings() -> dict:
    """Load user settings from ~/.jarvis/settings.json."""
    settings_path = os.path.expanduser("~/.jarvis/settings.json")
    if os.path.exists(settings_path):
        try:
            return json.loads(open(settings_path).read())
        except Exception:
            return {}
    return {}


def _save_settings(settings: dict):
    """Save user settings to ~/.jarvis/settings.json."""
    settings_path = os.path.expanduser("~/.jarvis/settings.json")
    os.makedirs(os.path.dirname(settings_path), exist_ok=True)
    with open(settings_path, "w") as f:
        json.dump(settings, f, indent=2)


@command("voice", description="Toggle voice mode on/off",
         usage="/voice [on|off|status]", category="core", permission=PermLevel.STANDARD)
async def cmd_voice(ctx: CommandContext) -> CommandResult:
    arg = ctx.args.strip().lower()
    brain = ctx.brain
    settings = _load_settings()

    if arg == "on":
        if brain:
            brain._voice_mode = True
        settings["voice_mode"] = True
        _save_settings(settings)
        return CommandResult(text="Voice mode: ON\n  TTS will read responses aloud.\n  Whisper will transcribe speech input.")
    elif arg == "off":
        if brain:
            brain._voice_mode = False
        settings["voice_mode"] = False
        _save_settings(settings)
        return CommandResult(text="Voice mode: OFF")
    else:
        # Status
        current = settings.get("voice_mode", False)
        if brain:
            current = getattr(brain, '_voice_mode', current)
        tts_available = False
        stt_available = False
        try:
            import edge_tts
            tts_available = True
        except ImportError:
            pass
        try:
            import whisper
            stt_available = True
        except ImportError:
            pass
        lines = [
            f"Voice mode: {'ON' if current else 'OFF'}",
            "",
            f"  TTS (edge-tts):   {'available' if tts_available else 'not installed (pip install edge-tts)'}",
            f"  STT (whisper):    {'available' if stt_available else 'not installed (pip install openai-whisper)'}",
            "",
            "  /voice on     Enable voice",
            "  /voice off    Disable voice",
        ]
        return CommandResult(text="\n".join(lines))


@command("vim", description="Toggle vim keybindings mode",
         usage="/vim [on|off]", category="core", permission=PermLevel.STANDARD)
async def cmd_vim(ctx: CommandContext) -> CommandResult:
    arg = ctx.args.strip().lower()
    brain = ctx.brain
    settings = _load_settings()

    if arg == "on":
        if brain:
            brain._vim_mode = True
        settings["vim_mode"] = True
        _save_settings(settings)
        return CommandResult(text="Vim mode: ON\n  ESC for normal mode, i for insert.\n  (Requires shell support for full vim bindings.)")
    elif arg == "off":
        if brain:
            brain._vim_mode = False
        settings["vim_mode"] = False
        _save_settings(settings)
        return CommandResult(text="Vim mode: OFF")
    else:
        current = settings.get("vim_mode", False)
        if brain:
            current = getattr(brain, '_vim_mode', current)
        return CommandResult(text=f"Vim mode: {'ON' if current else 'OFF'}\n  /vim on   Enable vim keybindings\n  /vim off  Disable vim keybindings")


@command("privacy", description="Privacy and telemetry settings",
         usage="/privacy [show|telemetry on|telemetry off]", category="core", permission=PermLevel.STANDARD)
async def cmd_privacy(ctx: CommandContext) -> CommandResult:
    arg = ctx.args.strip().lower()
    settings = _load_settings()

    if arg == "telemetry off":
        settings["telemetry"] = False
        _save_settings(settings)
        return CommandResult(text="Telemetry: OFF\n  No usage data will be collected or sent.")
    elif arg == "telemetry on":
        settings["telemetry"] = True
        _save_settings(settings)
        return CommandResult(text="Telemetry: ON\n  Anonymous usage stats may be collected.")
    else:
        # Show privacy overview
        telemetry = settings.get("telemetry", False)
        lines = [
            "JARVIS Privacy Settings",
            "=" * 40,
            "",
            f"  Telemetry:         {'ON' if telemetry else 'OFF'}",
            f"  Data storage:      Local only (~/.jarvis/)",
            f"  Conversation logs: ~/.jarvis/data/jarvis.db",
            f"  Feedback file:     ~/.jarvis/feedback.jsonl",
            f"  API calls:         Sent to configured LLM providers",
            "",
            "  JARVIS does not phone home. All data stays on your machine.",
            "  API calls go only to providers you have configured.",
            "",
            "  /privacy telemetry off   Disable telemetry",
            "  /privacy telemetry on    Enable telemetry",
        ]
        return CommandResult(text="\n".join(lines))


@command("rate-limit", aliases=["ratelimit"], description="Show rate limit status",
         usage="/rate-limit", category="core", permission=PermLevel.READ_ONLY)
async def cmd_rate_limit(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    lines = [
        "Rate Limit Status",
        "=" * 40,
    ]

    if brain and hasattr(brain, 'reasoner'):
        stats = brain.reasoner.usage_stats if hasattr(brain.reasoner, 'usage_stats') else {}
        calls = stats.get("calls", 0)
        model = stats.get("model", getattr(brain.reasoner, 'active_model_name', 'unknown'))
        inp = stats.get("input_tokens", 0)
        out = stats.get("output_tokens", 0)

        lines.extend([
            "",
            f"  Model:            {model}",
            f"  Session calls:    {calls}",
            f"  Input tokens:     {inp:,}",
            f"  Output tokens:    {out:,}",
            "",
            "  Provider Limits (typical)",
            "  " + "-" * 30,
            "  Groq:       30 req/min, 15K tokens/min (free)",
            "  Anthropic:  50 req/min, varies by tier",
            "  OpenAI:     varies by tier",
            "  Ollama:     unlimited (local)",
            "",
            "  If you hit rate limits, JARVIS will automatically retry",
            "  with exponential backoff. Use /fast to reduce token usage.",
        ])
    else:
        lines.append("  Brain not available for detailed stats.")
        lines.append("  Rate limits depend on your provider and tier.")

    return CommandResult(text="\n".join(lines))


@command("release-notes", aliases=["changelog"], description="Show recent changes and release notes",
         usage="/release-notes", category="core", permission=PermLevel.READ_ONLY)
async def cmd_release_notes(ctx: CommandContext) -> CommandResult:
    # Try reading CHANGELOG.md if it exists
    for changelog_path in [
        os.path.join(os.getcwd(), "CHANGELOG.md"),
        os.path.expanduser("~/.jarvis/CHANGELOG.md"),
    ]:
        if os.path.exists(changelog_path):
            try:
                with open(changelog_path, "r") as f:
                    content = f.read(3000)
                return CommandResult(text=f"Release Notes (from {changelog_path})\n{'=' * 40}\n{content}")
            except Exception:
                pass

    # Fallback: show recent git commits as release notes
    try:
        result = subprocess.run(
            ["git", "log", "--oneline", "--no-decorate", "-20"],
            capture_output=True, text=True, timeout=5
        )
        if result.stdout.strip():
            lines = [
                "Recent Changes (from git log)",
                "=" * 40,
                "",
            ]
            for line in result.stdout.strip().split('\n'):
                parts = line.split(' ', 1)
                if len(parts) == 2:
                    sha, msg = parts
                    lines.append(f"  {sha[:7]}  {msg}")
            return CommandResult(text="\n".join(lines))
    except Exception:
        pass

    # Hardcoded fallback
    notes = [
        "JARVIS v2.0 Release Notes",
        "=" * 40,
        "",
        "  Recent highlights:",
        "  - Desktop overlay with transparent GTK+WebKit window",
        "  - Computer use (mouse/keyboard control)",
        "  - Model routing with query_fast for speed",
        "  - 1M context support (Opus)",
        "  - Parallel tool execution",
        "  - Voice mode with Edge TTS + Whisper STT",
        "  - Persistent billing and cost tracking",
        "  - MCP server/client integration",
        "  - Neural Lattice memory with spreading activation",
        "  - Enhanced /doctor, /copy, /diff, /context commands",
        "",
        "  No CHANGELOG.md found. Create one at project root for custom notes.",
    ]
    return CommandResult(text="\n".join(notes))


@command("color", description="Set prompt bar color for this session",
         usage="/color [blue|green|yellow|magenta|cyan|red|white|default]", category="core")
async def cmd_color(ctx: CommandContext) -> CommandResult:
    """Set session color."""
    COLORS = ["blue", "green", "yellow", "magenta", "cyan", "red", "white"]
    RESET_ALIASES = {"default", "reset", "none", "gray", "grey"}

    args = ctx.args.strip().lower() if ctx.args else ""

    if not args or args == "status":
        current = "default"
        brain = ctx.brain
        if brain:
            current = getattr(brain, '_session_color', 'default')
        return CommandResult(text=f"Current color: {current}\nAvailable: {', '.join(COLORS + ['default'])}")

    if args in RESET_ALIASES:
        brain = ctx.brain
        if brain:
            brain._session_color = "default"
        return CommandResult(text="Color reset to default.")

    if args not in COLORS:
        return CommandResult(text=f"Unknown color: {args}\nAvailable: {', '.join(COLORS + ['default'])}")

    brain = ctx.brain
    if brain:
        brain._session_color = args
    return CommandResult(text=f"Session color set to: {args}")


# ── RAG — /ingest ────────────────────────────────────────────────────

@command(
    "ingest",
    aliases=["rag-add"],
    description="Ingest a file, URL, or directory into the local knowledge base (RAG)",
    usage="/ingest <path|url|.> [--clear] [--stats]",
    category="memory",
    permission=PermLevel.READ_ONLY,
)
async def cmd_ingest(ctx: CommandContext) -> CommandResult:
    """Add documents to JARVIS's local vector knowledge base."""
    args = ctx.args.strip()

    try:
        from src.rag import get_pipeline
        pipeline = get_pipeline()
    except Exception as e:
        return CommandResult(text=f"RAG unavailable: {e}", success=False)

    # /ingest --stats
    if args in ("--stats", "stats", ""):
        stats = pipeline.stats()
        if not args:
            return CommandResult(
                text=(
                    "Usage: /ingest <path|url|directory>\n\n"
                    "Examples:\n"
                    "  /ingest ~/Documents/notes.pdf\n"
                    "  /ingest https://docs.example.com/api\n"
                    "  /ingest ./src/  (entire directory)\n\n"
                    f"Current knowledge base: {stats['chunks']} chunks  "
                    f"({stats['collection']} via {stats['backend']})"
                )
            )
        return CommandResult(
            text=(
                f"RAG knowledge base stats:\n"
                f"  Chunks:    {stats['chunks']}\n"
                f"  Collection: {stats['collection']}\n"
                f"  Backend:    {stats['backend']}"
            )
        )

    # /ingest --clear
    if "--clear" in args:
        pipeline._store.clear()
        return CommandResult(text="Knowledge base cleared.")

    # Parse flags
    target = args.replace("--clear", "").strip()
    if not target:
        return CommandResult(text="Provide a path, URL, or directory.", success=False)

    # Determine type and ingest
    loop = asyncio.get_event_loop()

    if target.startswith("http://") or target.startswith("https://"):
        n = await loop.run_in_executor(None, pipeline.ingest_url, target)
        kind = "URL"
    else:
        resolved = os.path.expanduser(target)
        if os.path.isdir(resolved):
            n = await loop.run_in_executor(None, pipeline.ingest_directory, resolved)
            kind = "directory"
        elif os.path.isfile(resolved):
            n = await loop.run_in_executor(None, pipeline.ingest_file, resolved)
            kind = "file"
        else:
            return CommandResult(
                text=f"Path not found: {resolved}", success=False
            )

    if n == 0:
        return CommandResult(
            text=f"No content extracted from {kind}: {target}", success=False
        )

    stats = pipeline.stats()
    return CommandResult(
        text=(
            f"Ingested {n} chunks from {kind}: {target}\n"
            f"Knowledge base now holds {stats['chunks']} chunks total."
        )
    )
