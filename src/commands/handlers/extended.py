"""Extended commands -- misc utilities and settings."""

import json
from src.commands.registry import command, CommandContext, CommandResult, PermLevel


# ---------------------------------------------------------------------------
# Core / UI
# ---------------------------------------------------------------------------

@command("extra-usage", description="Configure extra usage to keep working when limits are hit",
         usage="/extra-usage [on|off|status]", category="core", permission=PermLevel.STANDARD)
async def cmd_extra_usage(ctx: CommandContext) -> CommandResult:
    """Toggle extended usage mode that continues operating when rate limits hit."""
    args = ctx.args.strip().lower() if ctx.args else "status"
    brain = ctx.brain

    if args == "status":
        enabled = getattr(brain, '_extra_usage_enabled', False) if brain else False
        return CommandResult(text=f"Extra usage: {'enabled' if enabled else 'disabled'}\n"
                            "When enabled, JARVIS will queue requests and retry when rate limits clear.")

    if args in ("on", "enable"):
        if brain:
            brain._extra_usage_enabled = True
        return CommandResult(text="Extra usage enabled. JARVIS will retry on rate-limit errors.")

    if args in ("off", "disable"):
        if brain:
            brain._extra_usage_enabled = False
        return CommandResult(text="Extra usage disabled.")

    return CommandResult(text="Usage: /extra-usage [on|off|status]", success=False)


# ---------------------------------------------------------------------------
# Files / Debug
# ---------------------------------------------------------------------------

@command("files", description="List all files currently in context",
         usage="/files", category="core", permission=PermLevel.READ_ONLY)
async def cmd_files(ctx: CommandContext) -> CommandResult:
    """Show files that are loaded into the current conversation context."""
    brain = ctx.brain
    if not brain:
        return CommandResult(text="No context available.")

    history = brain.memory.get_history(limit=200)
    file_set: set[str] = set()
    for entry in history:
        content = entry.get("content", "")
        if "read_file" in content or "write_file" in content or "edit_file" in content:
            import re
            paths = re.findall(r'(?:path|file)["\s:=]+([^\s"\']+)', content)
            file_set.update(p for p in paths if "/" in p or "." in p)

    if not file_set:
        return CommandResult(text="No files detected in current context.\n"
                            "Files appear here after read_file / write_file / edit_file tool calls.")

    lines = [f"Files in context ({len(file_set)})", "=" * 40]
    for f in sorted(file_set):
        lines.append(f"  {f}")
    return CommandResult(text="\n".join(lines))


@command("heapdump", description="Dump diagnostic heap info for debugging",
         usage="/heapdump", category="core", permission=PermLevel.FULL, hidden=True)
async def cmd_heapdump(ctx: CommandContext) -> CommandResult:
    """Dump memory usage info for debugging."""
    import gc

    gc.collect()

    objs = gc.get_objects()
    type_counts: dict[str, int] = {}
    for obj in objs:
        t = type(obj).__name__
        type_counts[t] = type_counts.get(t, 0) + 1

    top = sorted(type_counts.items(), key=lambda x: -x[1])[:20]
    lines = ["Heap Dump (top 20 types by count)", "=" * 50]
    for t, count in top:
        lines.append(f"  {t:<30s} {count:>8,}")
    lines.append(f"\n  Total tracked objects: {len(objs):,}")

    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith(("VmRSS:", "VmSize:", "VmPeak:")):
                    lines.append(f"  {line.strip()}")
    except Exception:
        pass

    return CommandResult(text="\n".join(lines))


@command("stats", description="Show JARVIS usage statistics and activity",
         usage="/stats", category="core", permission=PermLevel.READ_ONLY)
async def cmd_stats(ctx: CommandContext) -> CommandResult:
    """Show usage statistics across sessions."""
    brain = ctx.brain
    lines = ["Usage Statistics", "=" * 40]

    if brain:
        import time
        start = getattr(brain, '_session_start_time', None) or getattr(brain, '_init_time', None)
        if start:
            elapsed = time.time() - start
            hours, remainder = divmod(int(elapsed), 3600)
            mins, secs = divmod(remainder, 60)
            lines.append(f"  Session duration:  {hours}h {mins}m {secs}s")

        interactions = getattr(brain, '_interaction_count', 0)
        lines.append(f"  Interactions:      {interactions}")

        try:
            from src.agent.cost_tracker import get_tracker
            tracker = get_tracker()
            total_tokens = sum(u.total_tokens for u in tracker._model_usage.values())
            lines.append(f"  Total tokens:      {tracker.format_tokens(total_tokens)}")
            lines.append(f"  Session cost:      {tracker.format_cost(tracker.get_session_cost())}")
            lines.append(f"  Turns:             {tracker._turn_count}")
        except Exception:
            pass

        try:
            mem_stats = brain.memory.stats
            lines.append(f"  Memory nodes:      {mem_stats.get('lattice_nodes', 0)}")
            lines.append(f"  Memory synapses:   {mem_stats.get('lattice_synapses', 0)}")
        except Exception:
            pass
    else:
        lines.append("  Brain not available")

    return CommandResult(text="\n".join(lines))


# ---------------------------------------------------------------------------
# Plugins / Skills
# ---------------------------------------------------------------------------

@command("reload-plugins", description="Activate pending plugin changes in the current session",
         usage="/reload-plugins", category="plugin", permission=PermLevel.STANDARD)
async def cmd_reload_plugins(ctx: CommandContext) -> CommandResult:
    """Reload plugins without restarting JARVIS."""
    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available", success=False)

    try:
        count_before = len(brain.plugins.list_plugins())
        brain.plugins.load_all()
        count_after = len(brain.plugins.list_plugins())
        new = count_after - count_before
        msg = f"Plugins reloaded. {count_after} active"
        if new > 0:
            msg += f" ({new} new)"
        return CommandResult(text=msg)
    except Exception as e:
        return CommandResult(text=f"Reload failed: {e}", success=False)


# ---------------------------------------------------------------------------
# Remote / Integration
# ---------------------------------------------------------------------------

@command("remote-env", description="Configure the default remote environment for teleport sessions",
         usage="/remote-env [show|set <key> <value>]", category="core", permission=PermLevel.STANDARD)
async def cmd_remote_env(ctx: CommandContext) -> CommandResult:
    args = ctx.args.strip().split(maxsplit=2) if ctx.args else []
    sub = args[0].lower() if args else "show"

    try:
        from src.config import JARVIS_HOME
        env_path = JARVIS_HOME / "remote_env.json"
    except Exception:
        from pathlib import Path
        env_path = Path.home() / ".jarvis" / "remote_env.json"

    def _load():
        if env_path.exists():
            try:
                return json.loads(env_path.read_text())
            except Exception:
                return {}
        return {}

    def _save(data):
        env_path.parent.mkdir(parents=True, exist_ok=True)
        env_path.write_text(json.dumps(data, indent=2) + "\n")

    if sub == "show":
        data = _load()
        if not data:
            return CommandResult(text="No remote environment configured.\nUsage: /remote-env set <key> <value>")
        lines = ["Remote Environment", "=" * 40]
        for k, v in sorted(data.items()):
            lines.append(f"  {k}={v}")
        return CommandResult(text="\n".join(lines))

    if sub == "set" and len(args) >= 3:
        key, value = args[1], args[2]
        data = _load()
        data[key] = value
        _save(data)
        return CommandResult(text=f"Set remote env: {key}={value}")

    return CommandResult(text="Usage: /remote-env [show|set <key> <value>]", success=False)
