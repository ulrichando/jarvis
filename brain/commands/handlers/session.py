"""Session and history commands -- manage sessions, checkpoints, replay."""
import json
import time
from pathlib import Path

from brain.commands.registry import command, CommandContext, CommandResult, PermLevel


@command("session", aliases=["sess"], description="Manage sessions (list/new/save/delete/info)",
         usage="/session [list|new|save <name>|delete <id>|info]", category="session", permission=PermLevel.STANDARD)
async def cmd_session(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available", success=False)

    args = ctx.args.strip().split(maxsplit=1)
    sub = args[0].lower() if args else "info"
    rest = args[1].strip() if len(args) > 1 else ""

    sessions = brain.sessions

    if sub == "list":
        items = sessions.list_sessions()
        if not items:
            return CommandResult(text="No sessions found.")
        lines = ["Sessions", "=" * 60]
        for s in items:
            marker = " *" if s.get("active") else ""
            # Format timestamp
            updated = s.get("updated", 0)
            ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(updated)) if updated else "?"
            turns = s.get("turns", 0)
            name = s.get("name") or s.get("preview", "unnamed")
            if len(name) > 30:
                name = name[:27] + "..."
            lines.append(f"  [{s['id'][:8]}] {name:<30s} {turns:>3} turns  {ts}{marker}")
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

    elif sub == "info":
        # Show detailed info about current session
        current = getattr(sessions, 'current', None) or getattr(sessions, '_current', None)
        if not current:
            return CommandResult(text="No active session. Use /session new to start one.")

        elapsed = time.time() - current.created_at
        hours, remainder = divmod(int(elapsed), 3600)
        mins, secs = divmod(remainder, 60)
        duration = f"{hours}h {mins}m {secs}s" if hours else f"{mins}m {secs}s"

        msg_count = len(current.messages)
        user_turns = len([m for m in current.messages if m.get("role") == "user"])
        assistant_turns = len([m for m in current.messages if m.get("role") == "assistant"])

        # Count tool usage
        tool_calls = []
        for m in current.messages:
            if m.get("role") == "assistant" and "tool_calls" in m:
                for tc in m["tool_calls"]:
                    tool_calls.append(tc.get("function", {}).get("name", "unknown"))
        tool_counts = {}
        for t in tool_calls:
            tool_counts[t] = tool_counts.get(t, 0) + 1

        lines = [
            "Current Session",
            "=" * 40,
            f"  ID:         {current.id}",
            f"  Name:       {current.display_name}",
            f"  Mode:       {current.mode}",
            f"  Started:    {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(current.created_at))}",
            f"  Duration:   {duration}",
            f"  Messages:   {msg_count}",
            f"  User turns: {user_turns}",
            f"  Responses:  {assistant_turns}",
        ]

        if tool_counts:
            lines.append(f"\n  Tools used ({len(tool_calls)} calls)")
            lines.append("  " + "-" * 25)
            for tool, count in sorted(tool_counts.items(), key=lambda x: -x[1]):
                lines.append(f"    {tool}: {count}x")

        # Session cost if tracker is available
        try:
            from brain.agent.cost_tracker import get_tracker
            tracker = get_tracker()
            cost = tracker.get_session_cost()
            if cost > 0:
                lines.append(f"\n  Cost:       {tracker.format_cost(cost)}")
        except Exception:
            pass

        # Tags from metadata
        tags = current.metadata.get("tags", [])
        if tags:
            lines.append(f"  Tags:       {', '.join(tags)}")

        return CommandResult(text="\n".join(lines))

    else:
        return CommandResult(text=f"Unknown subcommand: {sub}. Use: list, new, save, delete, info", success=False)


@command("resume", aliases=["continue", "c"], description="Resume last or specific session",
         usage="/resume [--search <keyword>] [name_or_id]", category="session", permission=PermLevel.READ_ONLY)
async def cmd_resume(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available", success=False)

    args = ctx.args.strip()
    sessions = brain.sessions

    # Search mode: /resume --search <keyword>
    if args.startswith("--search"):
        keyword = args.replace("--search", "").strip()
        if not keyword:
            return CommandResult(text="Usage: /resume --search <keyword>", success=False)

        # Search through sessions for matching content
        all_sessions = sessions.list_sessions(limit=50)
        matches = []
        for s in all_sessions:
            name = s.get("name", "")
            preview = s.get("preview", "")
            if keyword.lower() in name.lower() or keyword.lower() in preview.lower():
                matches.append(s)

        if not matches:
            return CommandResult(text=f"No sessions matching '{keyword}'.")

        lines = [f"Sessions matching '{keyword}' ({len(matches)} found)", "=" * 50]
        for s in matches[:10]:
            ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(s.get("updated", 0))) if s.get("updated") else "?"
            name = s.get("name") or s.get("preview", "unnamed")
            if len(name) > 35:
                name = name[:32] + "..."
            lines.append(f"  [{s['id'][:8]}] {name:<35s} {s.get('turns', 0):>3} turns  {ts}")
        if len(matches) > 10:
            lines.append(f"  ... and {len(matches) - 10} more")
        lines.append(f"\nUse /resume <id> to resume a session.")
        return CommandResult(text="\n".join(lines))

    # No args: show recent sessions list, then resume latest
    if not args:
        recent = sessions.list_sessions(limit=5)
        if not recent:
            return CommandResult(text="No sessions to resume.", success=False)

        # Show a quick list of recent sessions
        lines = ["Recent sessions:"]
        for s in recent:
            ts = time.strftime("%m-%d %H:%M", time.localtime(s.get("updated", 0))) if s.get("updated") else "?"
            name = s.get("name") or s.get("preview", "unnamed")
            if len(name) > 40:
                name = name[:37] + "..."
            lines.append(f"  [{s['id'][:8]}] {name:<40s} {s.get('turns', 0)} turns  {ts}")

        # Resume the latest
        session = sessions.load_latest()
        if session:
            lines.insert(0, f"Resumed session: {session['id'][:8]} ({session.get('name', 'unnamed')})\n")
            return CommandResult(
                text="\n".join(lines),
                data={"session_id": session["id"]},
            )
        return CommandResult(text="\n".join(lines), success=False)

    # Specific target
    session = sessions.load_session(args)
    if not session:
        return CommandResult(text=f"No session found: {args}", success=False)

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


@command("export", description="Export current session to markdown, JSON, or text",
         usage="/export [--format json|markdown|text] [path]", category="session", permission=PermLevel.STANDARD)
async def cmd_export(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available", success=False)

    args = ctx.args.strip()
    history = brain.memory.get_history(limit=500)
    if not history:
        return CommandResult(text="Nothing to export.", success=False)

    # Parse --format flag
    fmt = None
    path_arg = args
    if "--format" in args:
        parts = args.split("--format", 1)
        path_arg = parts[0].strip()
        fmt_rest = parts[1].strip().split(maxsplit=1)
        if fmt_rest:
            fmt = fmt_rest[0].lower()
            if len(fmt_rest) > 1:
                path_arg = fmt_rest[1].strip()

    # Auto-detect format from extension if not explicit
    if not fmt and path_arg:
        if path_arg.endswith(".json"):
            fmt = "json"
        elif path_arg.endswith(".txt"):
            fmt = "text"
        else:
            fmt = "markdown"
    elif not fmt:
        fmt = "markdown"

    # Generate output in requested format
    if fmt == "json":
        output = json.dumps(history, indent=2, default=str)
    elif fmt == "text":
        lines = []
        for entry in history:
            role = entry.get("role", "unknown").upper()
            content = entry.get("content", "")
            ts = entry.get("timestamp", "")
            ts_str = f" [{time.strftime('%H:%M:%S', time.localtime(ts))}]" if ts else ""
            lines.append(f"[{role}]{ts_str}")
            lines.append(content)
            lines.append("")
        output = "\n".join(lines)
    else:
        # Markdown (default)
        lines = [
            "# JARVIS Session Export",
            f"*Exported: {time.strftime('%Y-%m-%d %H:%M:%S')}*",
            f"*Messages: {len(history)}*\n",
        ]
        for entry in history:
            role = entry.get("role", "unknown")
            content = entry.get("content", "")
            ts = entry.get("timestamp", "")
            ts_str = f" *{time.strftime('%H:%M:%S', time.localtime(ts))}*" if ts else ""
            lines.append(f"## {role.title()}{ts_str}\n\n{content}\n")
        output = "\n".join(lines)

    if path_arg:
        path = Path(path_arg).expanduser()
        path.write_text(output, encoding="utf-8")
        return CommandResult(text=f"Session exported to: {path} ({fmt} format, {len(history)} messages)")
    else:
        return CommandResult(text=output, data={"format": fmt})


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


@command("tag", description="Tag the current session for easy retrieval",
         usage="/tag add <tag> | /tag remove <tag> | /tag list", category="session", permission=PermLevel.STANDARD)
async def cmd_tag(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available", success=False)

    sessions = brain.sessions
    current = getattr(sessions, 'current', None) or getattr(sessions, '_current', None)
    if not current:
        return CommandResult(text="No active session to tag.", success=False)

    args = ctx.args.strip().split(maxsplit=1)
    sub = args[0].lower() if args else "list"
    rest = args[1].strip() if len(args) > 1 else ""

    # Ensure tags list exists in metadata
    if "tags" not in current.metadata:
        current.metadata["tags"] = []

    tags = current.metadata["tags"]

    if sub == "list":
        if not tags:
            return CommandResult(text="No tags on this session.")
        return CommandResult(text=f"Session tags: {', '.join(tags)}")

    elif sub == "add":
        if not rest:
            return CommandResult(text="Usage: /tag add <tag>", success=False)
        tag = rest.strip().lower().replace(" ", "-")
        if tag in tags:
            return CommandResult(text=f"Tag already exists: {tag}")
        tags.append(tag)
        current.metadata["tags"] = tags
        sessions.save_current()
        return CommandResult(text=f"Added tag: {tag} (session now has {len(tags)} tags)")

    elif sub == "remove":
        if not rest:
            return CommandResult(text="Usage: /tag remove <tag>", success=False)
        tag = rest.strip().lower().replace(" ", "-")
        if tag not in tags:
            return CommandResult(text=f"Tag not found: {tag}", success=False)
        tags.remove(tag)
        current.metadata["tags"] = tags
        sessions.save_current()
        return CommandResult(text=f"Removed tag: {tag}")

    else:
        return CommandResult(text=f"Unknown subcommand: {sub}. Use: add, remove, list", success=False)


@command("share", description="Generate a shareable session summary",
         usage="/share [--full]", category="session", permission=PermLevel.READ_ONLY)
async def cmd_share(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available", success=False)

    full_mode = "--full" in ctx.args

    history = brain.memory.get_history(limit=500)
    if not history:
        return CommandResult(text="Nothing to share -- session is empty.", success=False)

    # Extract user/assistant pairs
    pairs = []
    current_user = None
    for entry in history:
        role = entry.get("role", "")
        content = entry.get("content", "")
        if role == "user":
            current_user = content
        elif role in ("assistant", "jarvis") and current_user is not None:
            pairs.append((current_user, content))
            current_user = None

    if not pairs:
        return CommandResult(text="No complete exchanges found in session.")

    # Limit to first 5 exchanges unless --full
    show_pairs = pairs if full_mode else pairs[:5]
    truncated = not full_mode and len(pairs) > 5

    lines = [
        "Session Summary",
        "=" * 50,
        f"Total exchanges: {len(pairs)}",
        f"Showing: {'all' if full_mode else f'first {len(show_pairs)}'}",
        "",
    ]

    for i, (user_msg, assistant_msg) in enumerate(show_pairs, 1):
        # Truncate long messages
        user_preview = user_msg[:200] + ("..." if len(user_msg) > 200 else "")
        assist_preview = assistant_msg[:300] + ("..." if len(assistant_msg) > 300 else "")
        lines.append(f"--- Exchange {i} ---")
        lines.append(f"User: {user_preview}")
        lines.append(f"JARVIS: {assist_preview}")
        lines.append("")

    if truncated:
        lines.append(f"... {len(pairs) - 5} more exchanges. Use /share --full for complete summary.")

    return CommandResult(text="\n".join(lines))
