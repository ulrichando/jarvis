"""Session and history commands -- manage sessions, checkpoints, replay."""
import json
from pathlib import Path

from brain.commands.registry import command, CommandContext, CommandResult, PermLevel


@command("session", aliases=["sess"], description="Manage sessions (list/new/save/delete)",
         usage="/session [list|new|save <name>|delete <id>]", category="session", permission=PermLevel.STANDARD)
async def cmd_session(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available", success=False)

    args = ctx.args.strip().split(maxsplit=1)
    sub = args[0].lower() if args else "list"
    rest = args[1].strip() if len(args) > 1 else ""

    sessions = brain.sessions

    if sub == "list":
        items = sessions.list_sessions()
        if not items:
            return CommandResult(text="No sessions found.")
        lines = ["Sessions", "=" * 40]
        for s in items:
            marker = " *" if s.get("active") else ""
            lines.append(f"  [{s['id'][:8]}] {s.get('name', 'unnamed'):<20s} {s.get('created', '')}{marker}")
        return CommandResult(text="\n".join(lines))

    elif sub == "new":
        name = rest or None
        session = sessions.create_session(name=name)
        return CommandResult(text=f"New session created: {session['id'][:8]} ({session.get('name', 'unnamed')})")

    elif sub == "save":
        if not rest:
            return CommandResult(text="Usage: /session save <name>", success=False)
        sessions.save_current(name=rest)
        return CommandResult(text=f"Session saved as: {rest}")

    elif sub == "delete":
        if not rest:
            return CommandResult(text="Usage: /session delete <id>", success=False)
        sessions.delete_session(rest)
        return CommandResult(text=f"Session deleted: {rest}")

    else:
        return CommandResult(text=f"Unknown subcommand: {sub}. Use: list, new, save, delete", success=False)


@command("resume", aliases=["continue", "c"], description="Resume last or specific session",
         usage="/resume [name_or_id]", category="session", permission=PermLevel.READ_ONLY)
async def cmd_resume(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available", success=False)

    target = ctx.args.strip() or None
    sessions = brain.sessions

    if target:
        session = sessions.load_session(target)
    else:
        session = sessions.load_latest()

    if not session:
        return CommandResult(text="No session found to resume.", success=False)

    return CommandResult(
        text=f"Resumed session: {session['id'][:8]} ({session.get('name', 'unnamed')})",
        data={"session_id": session["id"]},
    )


@command("history", aliases=["hist"], description="Show conversation history",
         usage="/history [limit]", category="session", permission=PermLevel.READ_ONLY)
async def cmd_history(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available", success=False)

    args = ctx.args.strip()
    limit = int(args) if args.isdigit() else 20

    history = brain.memory.get_history(limit=limit)
    if not history:
        return CommandResult(text="No conversation history.")

    lines = [f"Last {len(history)} messages", "-" * 40]
    for i, entry in enumerate(history, 1):
        role = entry.get("role", "?").upper()
        content = entry.get("content", "")
        preview = content[:120].replace("\n", " ")
        if len(content) > 120:
            preview += "..."
        lines.append(f"  {i:>3}. [{role:<9s}] {preview}")
    return CommandResult(text="\n".join(lines))


@command("export", description="Export current session to markdown or JSON",
         usage="/export [path]", category="session", permission=PermLevel.STANDARD)
async def cmd_export(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available", success=False)

    args = ctx.args.strip()
    history = brain.memory.get_history(limit=500)
    if not history:
        return CommandResult(text="Nothing to export.", success=False)

    # Determine format from extension
    if args.endswith(".json"):
        output = json.dumps(history, indent=2, default=str)
    else:
        # Default to markdown
        lines = ["# JARVIS Session Export\n"]
        for entry in history:
            role = entry.get("role", "unknown")
            content = entry.get("content", "")
            lines.append(f"## {role.title()}\n\n{content}\n")
        output = "\n".join(lines)

    if args:
        path = Path(args).expanduser()
        path.write_text(output, encoding="utf-8")
        return CommandResult(text=f"Session exported to: {path}")
    else:
        return CommandResult(text=output, data={"format": "markdown"})


@command("import", description="Import session from file",
         usage="/import <path>", category="session", permission=PermLevel.STANDARD)
async def cmd_import(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available", success=False)

    args = ctx.args.strip()
    if not args:
        return CommandResult(text="Usage: /import <path>", success=False)

    path = Path(args).expanduser()
    if not path.exists():
        return CommandResult(text=f"File not found: {path}", success=False)

    content = path.read_text(encoding="utf-8")

    if path.suffix == ".json":
        data = json.loads(content)
        count = len(data) if isinstance(data, list) else 1
        brain.sessions.import_history(data)
        return CommandResult(text=f"Imported {count} entries from {path.name}")
    else:
        # Treat as plain text / markdown transcript
        brain.sessions.import_transcript(content)
        return CommandResult(text=f"Imported transcript from {path.name}")


@command("replay", description="Replay session tool calls (summary view)",
         usage="/replay [session_id]", category="session", permission=PermLevel.READ_ONLY)
async def cmd_replay(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available", success=False)

    session_id = ctx.args.strip() or None
    sessions = brain.sessions

    history = sessions.get_tool_calls(session_id=session_id)
    if not history:
        return CommandResult(text="No tool calls recorded in this session.")

    lines = [f"Tool Call Replay ({len(history)} calls)", "-" * 40]
    for i, call in enumerate(history, 1):
        tool = call.get("tool", "unknown")
        status = call.get("status", "?")
        summary = call.get("summary", "")[:80]
        lines.append(f"  {i:>3}. [{status:<7s}] {tool:<20s} {summary}")
    return CommandResult(text="\n".join(lines))


@command("snapshot", description="Save brain state (memory lattice + session)",
         usage="/snapshot", category="session", permission=PermLevel.STANDARD)
async def cmd_snapshot(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available", success=False)

    checkpoints = brain.checkpoints
    snap_id = checkpoints.create_snapshot(
        include_memory=True,
        include_session=True,
    )
    return CommandResult(text=f"Snapshot saved: {snap_id[:8]}")


@command("restore", description="Restore from snapshot",
         usage="/restore [snapshot_id]", category="session", permission=PermLevel.FULL)
async def cmd_restore(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available", success=False)

    snap_id = ctx.args.strip() or None
    checkpoints = brain.checkpoints

    if not snap_id:
        # Restore most recent
        snap_id = checkpoints.latest_snapshot_id()
        if not snap_id:
            return CommandResult(text="No snapshots available.", success=False)

    checkpoints.restore_snapshot(snap_id)
    return CommandResult(text=f"Restored from snapshot: {snap_id[:8]}")


@command("undo", description="Undo last file change via checkpoints",
         usage="/undo", category="session", permission=PermLevel.FULL)
async def cmd_undo(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available", success=False)

    checkpoints = brain.checkpoints
    result = checkpoints.undo_last()
    if result:
        path = result.get("path", "unknown")
        return CommandResult(text=f"Undone: {path}")
    return CommandResult(text="Nothing to undo.", success=False)
