"""Task management commands -- create, track, plan, and schedule tasks."""
import asyncio
import logging
import time
from datetime import datetime, timezone

from src.commands_brain.registry import command, CommandContext, CommandResult, PermLevel

log = logging.getLogger("jarvis.commands.task")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_task_mgr(brain):
    """Return brain.tasks (TaskManager) or None."""
    if brain and hasattr(brain, 'tasks'):
        return brain.tasks
    return None


def _format_task(t, verbose: bool = False) -> str:
    """Single-line task display.  Accepts Task object or dict."""
    if hasattr(t, 'as_dict'):
        d = t.as_dict()
    elif isinstance(t, dict):
        d = t
    else:
        d = {"id": str(t), "status": "unknown", "title": str(t)}

    status = d.get("status", "pending")
    icon = {"pending": "\u25cb", "in_progress": "\u27f3", "done": "\u2714", "failed": "\u2718"}
    prefix = icon.get(status, "\u25cb")
    priority = d.get("priority", "medium")
    pri_tag = f" !{priority}" if priority not in ("medium", "normal") else ""
    title = d.get("title", d.get("description", ""))[:60]
    line = f"  {prefix} {d['id'][:8]}  {title}{pri_tag}"
    if verbose:
        line += f"  ({status})"
    return line


def _elapsed_str(iso_start: str) -> str:
    """Human-readable elapsed time from an ISO timestamp to now."""
    try:
        start = datetime.fromisoformat(iso_start)
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - start
        secs = int(delta.total_seconds())
        if secs < 60:
            return f"{secs}s"
        elif secs < 3600:
            return f"{secs // 60}m {secs % 60}s"
        else:
            return f"{secs // 3600}h {(secs % 3600) // 60}m"
    except Exception:
        return "unknown"


# ---------------------------------------------------------------------------
# /task -- Show current task with status, progress, elapsed time
# ---------------------------------------------------------------------------

@command("task", aliases=["t"], description="Show current task or manage tasks",
         usage="/task [create|view|update|done] [args]", category="task", permission=PermLevel.STANDARD)
async def cmd_task(ctx: CommandContext) -> CommandResult:
    args = ctx.args.strip()
    brain = ctx.brain
    mgr = _get_task_mgr(brain)

    if not mgr:
        return CommandResult(text="Task manager not available.", success=False)

    # No args: show current (most recent in_progress) task
    if not args:
        in_progress = mgr.list_tasks(status_filter="in_progress", limit=1)
        if not in_progress:
            return CommandResult(text="No active task.")
        t = in_progress[0]
        elapsed = _elapsed_str(t.created_at)
        total = mgr.count()
        done_count = mgr.count(status_filter="done")
        progress = f"{done_count}/{total}" if total else "0/0"
        lines = [
            f"Current Task",
            f"  ID:       {t.id}",
            f"  Title:    {t.title}",
            f"  Status:   {t.status}",
            f"  Priority: {t.priority}",
            f"  Elapsed:  {elapsed}",
            f"  Progress: {progress} tasks done",
        ]
        if t.description:
            lines.append(f"  Desc:     {t.description}")
        if t.tags:
            lines.append(f"  Tags:     {t.tags}")
        return CommandResult(text="\n".join(lines))

    parts = args.split(None, 1)
    action = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""

    if action == "create":
        if not rest:
            return CommandResult(text="Usage: /task create <title>", success=False)
        task = mgr.create(title=rest)
        return CommandResult(text=f"Task created: {task.id} -- {rest}")

    elif action == "view":
        if not rest:
            return CommandResult(text="Usage: /task view <task_id>", success=False)
        task = mgr.get(rest)
        if not task:
            return CommandResult(text=f"Task {rest} not found.", success=False)
        elapsed = _elapsed_str(task.created_at)
        lines = [
            f"Task: {task.id}",
            f"  Title:    {task.title}",
            f"  Status:   {task.status}",
            f"  Priority: {task.priority}",
            f"  Created:  {task.created_at}",
            f"  Elapsed:  {elapsed}",
        ]
        if task.description:
            lines.append(f"  Desc:     {task.description}")
        if task.tags:
            lines.append(f"  Tags:     {task.tags}")
        return CommandResult(text="\n".join(lines))

    elif action == "update":
        tokens = rest.split(None, 1)
        if len(tokens) < 2:
            return CommandResult(text="Usage: /task update <task_id> <field=value ...>", success=False)
        task_id, updates_str = tokens
        updates = {}
        for pair in updates_str.split():
            if "=" in pair:
                k, v = pair.split("=", 1)
                updates[k] = v
        try:
            task = mgr.update(task_id, **updates)
        except ValueError as e:
            return CommandResult(text=str(e), success=False)
        if task:
            return CommandResult(text=f"Task {task_id[:8]} updated.")
        return CommandResult(text=f"Task {task_id} not found.", success=False)

    elif action == "done":
        if not rest:
            return CommandResult(text="Usage: /task done <task_id>", success=False)
        try:
            task = mgr.update_status(rest, "done")
        except ValueError as e:
            return CommandResult(text=str(e), success=False)
        if task:
            return CommandResult(text=f"Task {rest[:8]} marked done.")
        return CommandResult(text=f"Task {rest} not found.", success=False)

    elif action == "start":
        if not rest:
            return CommandResult(text="Usage: /task start <task_id>", success=False)
        try:
            task = mgr.update_status(rest, "in_progress")
        except ValueError as e:
            return CommandResult(text=str(e), success=False)
        if task:
            return CommandResult(text=f"Task {rest[:8]} started.")
        return CommandResult(text=f"Task {rest} not found.", success=False)

    else:
        return CommandResult(
            text=f"Unknown action: {action}. Use create, view, update, done, or start.",
            success=False,
        )


# ---------------------------------------------------------------------------
# /tasks -- List all tasks with status indicators and count
# ---------------------------------------------------------------------------

@command("tasks", aliases=["tl"], description="List tasks with status indicators and count",
         usage="/tasks [--status X] [--priority Y]", category="task", permission=PermLevel.READ_ONLY)
async def cmd_tasks(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    mgr = _get_task_mgr(brain)
    if not mgr:
        return CommandResult(text="Task manager not available.", success=False)

    args = ctx.args.strip()
    status_filter = None
    priority_filter = None

    tokens = args.split()
    i = 0
    while i < len(tokens):
        if tokens[i] == "--status" and i + 1 < len(tokens):
            status_filter = tokens[i + 1]
            i += 2
        elif tokens[i] == "--priority" and i + 1 < len(tokens):
            priority_filter = tokens[i + 1]
            i += 2
        else:
            i += 1

    all_tasks = mgr.list_tasks(status_filter=status_filter, priority_filter=priority_filter)
    if not all_tasks:
        return CommandResult(text="No tasks found.")

    # Count by status
    counts = {}
    for t in all_tasks:
        counts[t.status] = counts.get(t.status, 0) + 1

    status_summary = ", ".join(f"{v} {k}" for k, v in sorted(counts.items()))

    lines = [f"Tasks ({len(all_tasks)} total: {status_summary})", "=" * 50]
    for t in all_tasks:
        lines.append(_format_task(t, verbose=True))
    return CommandResult(text="\n".join(lines))


# ---------------------------------------------------------------------------
# /todo -- Manage TODO items: add, done, list, clear
# ---------------------------------------------------------------------------

@command("todo", description="Manage TODO items",
         usage="/todo [add <item> | done <number> | list | clear]", category="task",
         permission=PermLevel.STANDARD)
async def cmd_todo(ctx: CommandContext) -> CommandResult:
    text = ctx.args.strip()
    brain = ctx.brain
    mgr = _get_task_mgr(brain)

    if not mgr:
        return CommandResult(text="Task manager not available.", success=False)

    # No args or "list": show all pending TODOs
    if not text or text.lower() == "list":
        todos = mgr.list_tasks(status_filter="pending")
        if not todos:
            return CommandResult(text="No TODO items.")
        lines = [f"TODO ({len(todos)} items)", "-" * 40]
        for i, t in enumerate(todos, 1):
            lines.append(f"  {i}. [{t.id[:8]}] {t.title}")
        return CommandResult(text="\n".join(lines))

    parts = text.split(None, 1)
    action = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""

    if action == "add":
        if not rest:
            return CommandResult(text="Usage: /todo add <item>", success=False)
        task = mgr.create(title=rest, tags="todo")
        return CommandResult(text=f"TODO added: [{task.id[:8]}] {rest}")

    elif action == "done":
        if not rest:
            return CommandResult(text="Usage: /todo done <number>", success=False)
        todos = mgr.list_tasks(status_filter="pending")
        try:
            idx = int(rest) - 1
        except ValueError:
            # Maybe it is a task ID
            result = mgr.update_status(rest, "done")
            if result:
                return CommandResult(text=f"TODO {rest[:8]} marked done.")
            return CommandResult(text=f"Invalid number or ID: {rest}", success=False)

        if idx < 0 or idx >= len(todos):
            return CommandResult(text=f"Invalid number. Have {len(todos)} TODOs.", success=False)
        target = todos[idx]
        mgr.update_status(target.id, "done")
        return CommandResult(text=f"TODO done: {target.title}")

    elif action == "clear":
        todos = mgr.list_tasks(status_filter="pending")
        count = 0
        for t in todos:
            if "todo" in (t.tags or ""):
                mgr.delete(t.id)
                count += 1
        if count == 0:
            # Clear all pending if no tagged items
            for t in todos:
                mgr.update_status(t.id, "done")
                count += 1
        return CommandResult(text=f"Cleared {count} TODO items.")

    else:
        # Treat anything else as a quick add
        task = mgr.create(title=text, tags="todo")
        return CommandResult(text=f"TODO added: [{task.id[:8]}] {text}")


# ---------------------------------------------------------------------------
# /plan -- Enter plan mode or create a plan
# ---------------------------------------------------------------------------

@command("plan", description="Toggle plan mode or create a structured plan",
         usage="/plan [description]", category="task", permission=PermLevel.STANDARD)
async def cmd_plan(ctx: CommandContext) -> CommandResult:
    desc = ctx.args.strip()
    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available.", success=False)

    # No args: toggle plan mode or show current plan
    if not desc:
        current_mode = getattr(brain, '_mode', 'normal')
        if current_mode == 'plan':
            brain._mode = 'normal'
            return CommandResult(text="Plan mode OFF. Back to normal mode.")
        else:
            # Check if there is an existing plan
            current_plan = getattr(brain, '_current_plan', None)
            if current_plan:
                lines = ["Current Plan", "=" * 40]
                lines.append(f"  Goal: {current_plan.get('goal', 'N/A')}")
                steps = current_plan.get('steps', [])
                for i, step in enumerate(steps, 1):
                    done = step.get('done', False)
                    icon = "\u2714" if done else "\u25cb"
                    lines.append(f"  {icon} {i}. {step.get('text', '')}")
                lines.append(f"\nToggle plan mode: /plan")
                return CommandResult(text="\n".join(lines))
            brain._mode = 'plan'
            return CommandResult(
                text="Plan mode ON. I will analyze and plan without executing.\n"
                     "Use /plan <description> to create a new plan.\n"
                     "Use /plan again to toggle off."
            )

    # With args: create a plan using planner sub-agent
    try:
        from src.agent.loop import _run_sub_agent
    except ImportError:
        return CommandResult(text="Agent loop not available.", success=False)

    prompt = (
        f"Create a detailed, structured plan for the following goal. "
        f"Break it into numbered steps with clear deliverables.\n\nGoal: {desc}"
    )

    try:
        result = await _run_sub_agent(brain.reasoner, "planner", prompt)
    except Exception as e:
        return CommandResult(text=f"Plan generation failed: {e}", success=False)

    # Store the plan on the brain for later reference
    steps = []
    for line in result.splitlines():
        stripped = line.strip()
        if stripped and (stripped[0].isdigit() or stripped.startswith("-")):
            steps.append({"text": stripped.lstrip("0123456789.-) "), "done": False})

    brain._current_plan = {"goal": desc, "steps": steps, "raw": result, "created_at": time.time()}

    return CommandResult(text=f"Plan for: {desc}\n{'=' * 40}\n{result}")


# ---------------------------------------------------------------------------
# /ultraplan -- Enhanced planning with sub-agent research
# ---------------------------------------------------------------------------

@command("ultraplan", aliases=["up"], description="Deep planning with web research (scout + planner)",
         usage="/ultraplan <goal>", category="task", permission=PermLevel.FULL)
async def cmd_ultraplan(ctx: CommandContext) -> CommandResult:
    desc = ctx.args.strip()
    if not desc:
        return CommandResult(text="Usage: /ultraplan <goal>", success=False)

    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available.", success=False)

    try:
        from src.agent.loop import _run_sub_agent
    except ImportError:
        return CommandResult(text="Agent loop not available.", success=False)

    # Phase 1: scout gathers context
    scout_prompt = (
        f"Research the following topic thoroughly. Find relevant files, code, "
        f"documentation, and web resources.\n\nTopic: {desc}"
    )
    try:
        research = await _run_sub_agent(brain.reasoner, "scout", scout_prompt)
    except Exception as e:
        return CommandResult(text=f"Scout research failed: {e}", success=False)

    # Phase 2: planner creates plan using research
    plan_prompt = (
        f"Using the following research, create a comprehensive implementation plan "
        f"with phases, tasks, dependencies, and risk mitigations.\n\n"
        f"Research:\n{research}\n\nGoal: {desc}"
    )
    try:
        plan = await _run_sub_agent(brain.reasoner, "planner", plan_prompt)
    except Exception as e:
        return CommandResult(text=f"Planning failed: {e}", success=False)

    # Store as current plan
    brain._current_plan = {"goal": desc, "steps": [], "raw": plan, "created_at": time.time()}

    research_summary = research[:500] + ("..." if len(research) > 500 else "")
    return CommandResult(
        text=f"Ultra Plan: {desc}\n{'=' * 50}\n\n"
             f"Research Summary:\n{research_summary}\n\n"
             f"Plan:\n{plan}"
    )


# ---------------------------------------------------------------------------
# /sprint -- Sprint management: start, status, done
# ---------------------------------------------------------------------------

@command("sprint", description="Sprint management",
         usage="/sprint <start <goal> | status | done>", category="task", permission=PermLevel.STANDARD)
async def cmd_sprint(ctx: CommandContext) -> CommandResult:
    args = ctx.args.strip()
    brain = ctx.brain
    mgr = _get_task_mgr(brain)

    if not mgr:
        return CommandResult(text="Task manager not available.", success=False)

    if not args:
        return CommandResult(
            text="Usage:\n"
                 "  /sprint start <goal>           Start a new sprint\n"
                 "  /sprint status                  Show sprint progress\n"
                 "  /sprint done                    Complete the current sprint\n"
                 "  /sprint add <task1; task2; ...>  Add tasks to current sprint",
            success=False,
        )

    parts = args.split(None, 1)
    action = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""

    # Initialize sprint storage on brain
    if not hasattr(brain, '_sprints'):
        brain._sprints = []

    if action == "start":
        if not rest:
            return CommandResult(text="Usage: /sprint start <goal>", success=False)
        sprint = {
            "name": rest,
            "started_at": time.time(),
            "status": "active",
            "task_ids": [],
        }
        brain._sprints.append(sprint)
        return CommandResult(
            text=f"Sprint started: {rest}\n"
                 f"Use '/sprint add <task1; task2; ...>' to add tasks."
        )

    elif action == "status":
        if not brain._sprints:
            return CommandResult(text="No sprints. Use /sprint start <goal> to begin.")
        current = brain._sprints[-1]
        lines = [
            f"Sprint: {current['name']}",
            f"  Status:  {current['status']}",
            f"  Started: {_elapsed_str(datetime.fromtimestamp(current['started_at'], tz=timezone.utc).isoformat())} ago",
        ]
        task_ids = current.get("task_ids", [])
        if task_ids:
            done_count = 0
            for tid in task_ids:
                t = mgr.get(tid)
                if t:
                    lines.append(_format_task(t, verbose=True))
                    if t.status == "done":
                        done_count += 1
            total = len(task_ids)
            pct = int((done_count / total) * 100) if total else 0
            lines.insert(3, f"  Progress: {done_count}/{total} ({pct}%)")
        else:
            lines.append("  No tasks added yet.")
        return CommandResult(text="\n".join(lines))

    elif action == "done":
        if not brain._sprints:
            return CommandResult(text="No active sprint.", success=False)
        current = brain._sprints[-1]
        if current["status"] != "active":
            return CommandResult(text="No active sprint.", success=False)
        current["status"] = "completed"
        current["completed_at"] = time.time()
        # Mark remaining tasks done
        for tid in current.get("task_ids", []):
            t = mgr.get(tid)
            if t and t.status != "done":
                try:
                    mgr.update_status(tid, "done")
                except Exception:
                    pass
        elapsed = time.time() - current["started_at"]
        return CommandResult(
            text=f"Sprint completed: {current['name']}\n"
                 f"  Duration: {int(elapsed)}s\n"
                 f"  Tasks: {len(current.get('task_ids', []))}"
        )

    elif action == "add":
        if not rest:
            return CommandResult(text="Usage: /sprint add <task1; task2; ...>", success=False)
        if not brain._sprints or brain._sprints[-1]["status"] != "active":
            return CommandResult(text="No active sprint. Use /sprint start <goal> first.", success=False)
        current = brain._sprints[-1]
        task_titles = [t.strip() for t in rest.split(";") if t.strip()]
        created = []
        for title in task_titles:
            task = mgr.create(title=title, tags=f"sprint:{current['name']}")
            current["task_ids"].append(task.id)
            created.append(task)
        lines = [f"Added {len(created)} tasks to sprint '{current['name']}':", "-" * 40]
        for t in created:
            lines.append(_format_task(t))
        return CommandResult(text="\n".join(lines))

    else:
        return CommandResult(
            text=f"Unknown sprint action: {action}. Use start, status, done, or add.",
            success=False,
        )


# ---------------------------------------------------------------------------
# /schedule -- Schedule a task for later
# ---------------------------------------------------------------------------

@command("schedule", aliases=["cron"], description="Schedule a task",
         usage="/schedule <time> <task> | /schedule list", category="task", permission=PermLevel.FULL)
async def cmd_schedule(ctx: CommandContext) -> CommandResult:
    args = ctx.args.strip()
    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available.", success=False)

    if not hasattr(brain, '_scheduled_tasks'):
        brain._scheduled_tasks = []

    if not args or args.lower() == "list":
        if not brain._scheduled_tasks:
            return CommandResult(text="No scheduled tasks.")
        lines = [f"Scheduled Tasks ({len(brain._scheduled_tasks)})", "=" * 40]
        for entry in brain._scheduled_tasks:
            active_tag = " [active]" if entry.get("active") else " [cancelled]"
            lines.append(f"  {entry['id']}  {entry['cron']}  {entry['task']}{active_tag}")
        return CommandResult(text="\n".join(lines))

    # Parse: first token (possibly quoted) is cron/time expression, rest is task
    import shlex
    try:
        tokens = shlex.split(args)
    except ValueError:
        return CommandResult(text="Could not parse arguments. Quote the time expression.", success=False)

    if len(tokens) < 2:
        return CommandResult(
            text="Usage:\n"
                 "  /schedule <cron_expr> <task description>\n"
                 "  /schedule list\n"
                 "Example: /schedule '*/30 * * * *' check system health",
            success=False,
        )

    cron_expr = tokens[0]
    task_desc = " ".join(tokens[1:])

    schedule_id = f"sched-{len(brain._scheduled_tasks):03d}"
    entry = {
        "id": schedule_id,
        "cron": cron_expr,
        "task": task_desc,
        "created_at": time.time(),
        "active": True,
    }
    brain._scheduled_tasks.append(entry)

    return CommandResult(
        text=f"Scheduled task created: {schedule_id}\n"
             f"  Schedule: {cron_expr}\n"
             f"  Task:     {task_desc}",
    )


# ---------------------------------------------------------------------------
# /background -- Run a task in background
# ---------------------------------------------------------------------------

@command("background", aliases=["bg"], description="Run a task in the background",
         usage="/background <query> | /background (show running)", category="task", permission=PermLevel.FULL)
async def cmd_background(ctx: CommandContext) -> CommandResult:
    desc = ctx.args.strip()
    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available.", success=False)

    if not hasattr(brain, '_background_tasks'):
        brain._background_tasks = {}

    # No args: show running background tasks
    if not desc:
        bg = brain._background_tasks
        if not bg:
            return CommandResult(text="No background tasks running.")
        lines = [f"Background Tasks ({len(bg)})", "=" * 40]
        for tid, info in bg.items():
            state = "done" if info['task'].done() else "running"
            lines.append(f"  [{tid}] {info['type']:<10s} {state:<10s} {info['desc'][:50]}")
        return CommandResult(text="\n".join(lines))

    # Create a tracked task
    mgr = _get_task_mgr(brain)
    task_record = None
    if mgr:
        task_record = mgr.create(title=desc, tags="background")
        mgr.update_status(task_record.id, "in_progress")

    # Spawn background agent
    try:
        from src.agent.loop import _run_sub_agent
    except ImportError:
        return CommandResult(text="Agent loop not available.", success=False)

    async def _bg_run():
        try:
            result = await _run_sub_agent(brain.reasoner, "worker", desc)
            if mgr and task_record:
                mgr.update_status(task_record.id, "done")
            return result
        except Exception as e:
            log.error("Background task failed: %s", e)
            if mgr and task_record:
                mgr.update_status(task_record.id, "failed")

    bg_task = asyncio.create_task(_bg_run())
    task_id = task_record.id[:8] if task_record else str(id(bg_task))
    brain._background_tasks[task_id] = {"task": bg_task, "type": "worker", "desc": desc}

    return CommandResult(
        text=f"Background task started: {task_id}\n"
             f"  Description: {desc}\n"
             f"  Use /background to list running tasks.\n"
             f"  Use /cancel {task_id} to stop.",
    )


# ---------------------------------------------------------------------------
# /cancel -- Cancel running task
# ---------------------------------------------------------------------------

@command("cancel", description="Cancel a running or scheduled task",
         usage="/cancel [task-id]", category="task", permission=PermLevel.STANDARD)
async def cmd_cancel(ctx: CommandContext) -> CommandResult:
    task_id = ctx.args.strip()
    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available.", success=False)

    # No ID: cancel most recent background task
    if not task_id:
        bg = getattr(brain, '_background_tasks', {})
        if bg:
            last_id = list(bg.keys())[-1]
            bg[last_id]['task'].cancel()
            info = bg.pop(last_id)
            return CommandResult(text=f"Cancelled most recent background task: {last_id}\n  Was: {info['desc']}")
        return CommandResult(text="No running tasks to cancel. Provide a task ID.", success=False)

    # Check scheduled tasks
    scheduled = getattr(brain, '_scheduled_tasks', [])
    for entry in scheduled:
        if entry['id'] == task_id:
            entry['active'] = False
            return CommandResult(text=f"Scheduled task {task_id} cancelled.")

    # Check background tasks
    bg = getattr(brain, '_background_tasks', {})
    if task_id in bg:
        bg[task_id]['task'].cancel()
        info = bg.pop(task_id)
        return CommandResult(text=f"Background task {task_id} cancelled.\n  Was: {info['desc']}")

    # Check task manager
    mgr = _get_task_mgr(brain)
    if mgr:
        try:
            task = mgr.update_status(task_id, "failed")
            if task:
                return CommandResult(text=f"Task {task_id[:8]} cancelled.")
        except Exception:
            pass

    return CommandResult(text=f"Task {task_id} not found.", success=False)


# ---------------------------------------------------------------------------
# /backlog -- Show and manage backlog items
# ---------------------------------------------------------------------------

@command("backlog", description="Manage backlog items",
         usage="/backlog [add <item> | list]", category="task", permission=PermLevel.READ_ONLY)
async def cmd_backlog(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    mgr = _get_task_mgr(brain)
    if not mgr:
        return CommandResult(text="Task manager not available.", success=False)

    args = ctx.args.strip()

    if args:
        parts = args.split(None, 1)
        action = parts[0].lower()
        rest = parts[1] if len(parts) > 1 else ""

        if action == "add":
            if not rest:
                return CommandResult(text="Usage: /backlog add <item>", success=False)
            task = mgr.create(title=rest, priority="low", tags="backlog")
            return CommandResult(text=f"Backlog item added: [{task.id[:8]}] {rest}")

        elif action != "list":
            # Treat as quick-add
            task = mgr.create(title=args, priority="low", tags="backlog")
            return CommandResult(text=f"Backlog item added: [{task.id[:8]}] {args}")

    # List backlog (pending + in_progress, sorted by priority)
    pending = mgr.list_tasks(status_filter="pending")
    in_progress = mgr.list_tasks(status_filter="in_progress")
    all_items = pending + in_progress

    if not all_items:
        return CommandResult(text="Backlog is empty.")

    # Sort by priority
    priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    all_items.sort(key=lambda t: priority_order.get(t.priority, 2))

    lines = [f"Backlog ({len(all_items)} items)", "=" * 40]
    for t in all_items:
        lines.append(_format_task(t, verbose=True))
    return CommandResult(text="\n".join(lines))
