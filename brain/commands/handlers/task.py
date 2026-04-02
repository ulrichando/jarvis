"""Task management commands — create, track, plan, and schedule tasks."""
import asyncio
import logging
import time

from brain.commands.registry import command, CommandContext, CommandResult, PermLevel

log = logging.getLogger("jarvis.commands.task")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_task_mgr(brain):
    """Return brain.tasks (TaskManager) or None."""
    if brain and hasattr(brain, 'tasks'):
        return brain.tasks
    return None


def _format_task(t: dict, verbose: bool = False) -> str:
    """Single-line task display."""
    status_icon = {"todo": "[ ]", "in_progress": "[~]", "done": "[x]", "cancelled": "[-]"}
    icon = status_icon.get(t.get("status", "todo"), "[ ]")
    priority = t.get("priority", "normal")
    pri_tag = f" !{priority}" if priority != "normal" else ""
    line = f"  {icon} {t['id'][:8]}  {t.get('title', t.get('description', ''))[:50]}{pri_tag}"
    if verbose:
        line += f"  ({t.get('status', 'todo')})"
    return line


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

@command("task", aliases=["t"], description="Create, view, update, or complete a task",
         usage="/task <create|view|update|done> <args>", category="task", permission=PermLevel.STANDARD)
async def cmd_task(ctx: CommandContext) -> CommandResult:
    args = ctx.args.strip()
    if not args:
        return CommandResult(text="Usage: /task <create|view|update|done> <args>", success=False)

    brain = ctx.brain
    mgr = _get_task_mgr(brain)
    if not mgr:
        return CommandResult(text="Task manager not available", success=False)

    parts = args.split(None, 1)
    action = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""

    if action == "create":
        if not rest:
            return CommandResult(text="Usage: /task create <title>", success=False)
        task = mgr.create(title=rest)
        return CommandResult(text=f"Task created: {task['id'][:8]} — {rest}")

    elif action == "view":
        if not rest:
            return CommandResult(text="Usage: /task view <task_id>", success=False)
        task = mgr.get(rest)
        if not task:
            return CommandResult(text=f"Task {rest} not found.", success=False)
        lines = [
            f"Task: {task['id']}",
            f"  Title:    {task.get('title', 'N/A')}",
            f"  Status:   {task.get('status', 'todo')}",
            f"  Priority: {task.get('priority', 'normal')}",
            f"  Created:  {task.get('created_at', 'N/A')}",
        ]
        if task.get('description'):
            lines.append(f"  Desc:     {task['description']}")
        if task.get('tags'):
            lines.append(f"  Tags:     {', '.join(task['tags'])}")
        return CommandResult(text="\n".join(lines))

    elif action == "update":
        tokens = rest.split(None, 1)
        if len(tokens) < 2:
            return CommandResult(text="Usage: /task update <task_id> <field=value ...>", success=False)
        task_id, updates_str = tokens
        # Parse key=value pairs
        updates = {}
        for pair in updates_str.split():
            if "=" in pair:
                k, v = pair.split("=", 1)
                updates[k] = v
        task = mgr.update(task_id, **updates)
        if task:
            return CommandResult(text=f"Task {task_id[:8]} updated.")
        return CommandResult(text=f"Task {task_id} not found.", success=False)

    elif action == "done":
        if not rest:
            return CommandResult(text="Usage: /task done <task_id>", success=False)
        task = mgr.update(rest, status="done")
        if task:
            return CommandResult(text=f"Task {rest[:8]} marked done.")
        return CommandResult(text=f"Task {rest} not found.", success=False)

    else:
        return CommandResult(text=f"Unknown action: {action}. Use create, view, update, or done.", success=False)


@command("tasks", aliases=["tl"], description="List tasks with optional filters",
         usage="/tasks [--status X] [--priority Y]", category="task", permission=PermLevel.READ_ONLY)
async def cmd_tasks(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    mgr = _get_task_mgr(brain)
    if not mgr:
        return CommandResult(text="Task manager not available", success=False)

    args = ctx.args.strip()
    status_filter = None
    priority_filter = None

    # Simple arg parsing
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

    all_tasks = mgr.list(status_filter=status_filter, priority_filter=priority_filter)
    if not all_tasks:
        return CommandResult(text="No tasks found.")

    lines = [f"Tasks ({len(all_tasks)})", "=" * 40]
    for t in all_tasks:
        lines.append(_format_task(t, verbose=True))
    return CommandResult(text="\n".join(lines))


@command("todo", description="Quick-add a TODO task",
         usage="/todo <text>", category="task", permission=PermLevel.STANDARD)
async def cmd_todo(ctx: CommandContext) -> CommandResult:
    text = ctx.args.strip()
    if not text:
        return CommandResult(text="Usage: /todo <task description>", success=False)

    brain = ctx.brain
    mgr = _get_task_mgr(brain)
    if not mgr:
        return CommandResult(text="Task manager not available", success=False)

    task = mgr.create(title=text, priority="normal")
    return CommandResult(text=f"TODO added: {task['id'][:8]} — {text}")


@command("plan", description="Generate a structured plan using planner agent",
         usage="/plan <description>", category="task", permission=PermLevel.STANDARD)
async def cmd_plan(ctx: CommandContext) -> CommandResult:
    desc = ctx.args.strip()
    if not desc:
        return CommandResult(text="Usage: /plan <description of what to plan>", success=False)

    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available", success=False)

    prompt = (
        f"Create a detailed, structured plan for the following goal. "
        f"Break it into numbered steps with clear deliverables.\n\nGoal: {desc}"
    )

    from brain.agent.loop import _run_sub_agent
    result = await _run_sub_agent(brain.reasoner, "planner", prompt)
    return CommandResult(text=f"Plan for: {desc}\n{'=' * 40}\n{result}")


@command("ultraplan", aliases=["up"], description="Deep planning with web research (scout + planner)",
         usage="/ultraplan <description>", category="task", permission=PermLevel.FULL)
async def cmd_ultraplan(ctx: CommandContext) -> CommandResult:
    desc = ctx.args.strip()
    if not desc:
        return CommandResult(text="Usage: /ultraplan <description>", success=False)

    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available", success=False)

    from brain.agent.loop import _run_sub_agent

    # Phase 1: scout gathers context
    scout_prompt = (
        f"Research the following topic thoroughly. Find relevant files, code, "
        f"documentation, and web resources.\n\nTopic: {desc}"
    )
    research = await _run_sub_agent(brain.reasoner, "scout", scout_prompt)

    # Phase 2: planner creates plan using research
    plan_prompt = (
        f"Using the following research, create a comprehensive implementation plan "
        f"with phases, tasks, dependencies, and risk mitigations.\n\n"
        f"Research:\n{research}\n\nGoal: {desc}"
    )
    plan = await _run_sub_agent(brain.reasoner, "planner", plan_prompt)

    return CommandResult(text=f"Ultra Plan: {desc}\n{'=' * 50}\n\nResearch Summary:\n{research[:500]}...\n\nPlan:\n{plan}")


@command("backlog", description="Show pending tasks sorted by priority",
         usage="/backlog", category="task", permission=PermLevel.READ_ONLY)
async def cmd_backlog(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    mgr = _get_task_mgr(brain)
    if not mgr:
        return CommandResult(text="Task manager not available", success=False)

    pending = mgr.list(status_filter="todo") + mgr.list(status_filter="in_progress")
    if not pending:
        return CommandResult(text="Backlog is empty.")

    priority_order = {"critical": 0, "high": 1, "normal": 2, "low": 3}
    pending.sort(key=lambda t: priority_order.get(t.get("priority", "normal"), 2))

    lines = [f"Backlog ({len(pending)} items)", "=" * 40]
    for t in pending:
        lines.append(_format_task(t, verbose=True))
    return CommandResult(text="\n".join(lines))


@command("sprint", description="Create a batch of related tasks",
         usage="/sprint <name> <task1; task2; task3>", category="task", permission=PermLevel.STANDARD)
async def cmd_sprint(ctx: CommandContext) -> CommandResult:
    args = ctx.args.strip()
    if not args:
        return CommandResult(text='Usage: /sprint <name> <task1; task2; task3>', success=False)

    parts = args.split(None, 1)
    sprint_name = parts[0]
    task_text = parts[1] if len(parts) > 1 else ""
    if not task_text:
        return CommandResult(text="Provide semicolon-separated tasks.", success=False)

    brain = ctx.brain
    mgr = _get_task_mgr(brain)
    if not mgr:
        return CommandResult(text="Task manager not available", success=False)

    task_titles = [t.strip() for t in task_text.split(";") if t.strip()]
    created = []
    for title in task_titles:
        task = mgr.create(title=title, tags=[f"sprint:{sprint_name}"])
        created.append(task)

    lines = [f"Sprint '{sprint_name}' created with {len(created)} tasks:", "=" * 40]
    for t in created:
        lines.append(_format_task(t))
    return CommandResult(text="\n".join(lines))


@command("background", aliases=["bg"], description="Run a task in the background via agent",
         usage="/background <task_description>", category="task", permission=PermLevel.FULL)
async def cmd_background(ctx: CommandContext) -> CommandResult:
    desc = ctx.args.strip()
    if not desc:
        return CommandResult(text="Usage: /background <task description>", success=False)

    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available", success=False)

    # Create a tracked task
    mgr = _get_task_mgr(brain)
    task_record = None
    if mgr:
        task_record = mgr.create(title=desc, status="in_progress", tags=["background"])

    # Spawn background agent
    from brain.agent.loop import _run_sub_agent

    async def _bg_run():
        try:
            result = await _run_sub_agent(brain.reasoner, "worker", desc)
            if mgr and task_record:
                mgr.update(task_record['id'], status="done", result=result[:500])
            return result
        except Exception as e:
            log.error("Background task failed: %s", e)
            if mgr and task_record:
                mgr.update(task_record['id'], status="cancelled", error=str(e))

    bg_task = asyncio.create_task(_bg_run())
    if not hasattr(brain, '_background_tasks'):
        brain._background_tasks = {}
    task_id = task_record['id'][:8] if task_record else str(id(bg_task))
    brain._background_tasks[task_id] = {"task": bg_task, "type": "worker", "desc": desc}

    return CommandResult(
        text=f"Background task started: {task_id}\nDescription: {desc}\nUse /agent-status to monitor.",
    )


@command("schedule", aliases=["cron"], description="Schedule a recurring task",
         usage="/schedule <cron_expr> <task description>", category="task", permission=PermLevel.FULL)
async def cmd_schedule(ctx: CommandContext) -> CommandResult:
    args = ctx.args.strip()
    if not args:
        return CommandResult(
            text="Usage: /schedule <cron_expr> <task description>\n"
                 "Example: /schedule '*/30 * * * *' check system health",
            success=False,
        )

    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available", success=False)

    # Parse: first token (possibly quoted) is cron expression, rest is task
    import shlex
    try:
        tokens = shlex.split(args)
    except ValueError:
        return CommandResult(text="Could not parse arguments. Quote the cron expression.", success=False)

    if len(tokens) < 2:
        return CommandResult(text="Provide both a cron expression and a task description.", success=False)

    cron_expr = tokens[0]
    task_desc = " ".join(tokens[1:])

    # Store in scheduler registry
    if not hasattr(brain, '_scheduled_tasks'):
        brain._scheduled_tasks = []

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
        text=f"Scheduled task created: {schedule_id}\nCron: {cron_expr}\nTask: {task_desc}",
    )


@command("cancel", description="Cancel a running or scheduled task",
         usage="/cancel <task_id>", category="task", permission=PermLevel.STANDARD)
async def cmd_cancel(ctx: CommandContext) -> CommandResult:
    task_id = ctx.args.strip()
    if not task_id:
        return CommandResult(text="Usage: /cancel <task_id>", success=False)

    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available", success=False)

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
        del bg[task_id]
        return CommandResult(text=f"Background task {task_id} cancelled.")

    # Check task manager
    mgr = _get_task_mgr(brain)
    if mgr:
        task = mgr.update(task_id, status="cancelled")
        if task:
            return CommandResult(text=f"Task {task_id[:8]} cancelled.")

    return CommandResult(text=f"Task {task_id} not found.", success=False)
