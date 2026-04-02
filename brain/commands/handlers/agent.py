"""Agent & Team commands — spawn, coordinate, and manage agents."""
import asyncio
import logging

from brain.commands.registry import command, CommandContext, CommandResult, PermLevel

log = logging.getLogger("jarvis.commands.agent")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_coordinator(brain):
    """Return the brain's coordinator if available."""
    if brain and hasattr(brain, '_coordinator'):
        return brain._coordinator
    return None


async def _spawn_shortcut(ctx: CommandContext, agent_type: str) -> CommandResult:
    """Shared logic for /scout, /worker, /planner shortcuts."""
    task = ctx.args.strip()
    if not task:
        return CommandResult(text=f"Usage: /{agent_type} <task description>", success=False)

    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available", success=False)

    coordinator = _get_coordinator(brain)
    if coordinator:
        handle = await coordinator.spawn_agent(brain.reasoner, agent_type, task)
        return CommandResult(text=f"Agent spawned: {handle['id']} ({agent_type})\nTask: {task}\nStatus: running")

    from brain.agent.loop import _run_sub_agent
    result = await _run_sub_agent(brain.reasoner, agent_type, task)
    return CommandResult(text=result)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

@command("agent", description="Spawn a named agent with a task",
         usage="/agent <type> <task>", category="agent", permission=PermLevel.FULL)
async def cmd_agent(ctx: CommandContext) -> CommandResult:
    args = ctx.args.strip()
    if not args:
        return CommandResult(text="Usage: /agent <scout|worker|planner> <task description>", success=False)
    parts = args.split(None, 1)
    agent_type = parts[0].lower()
    task = parts[1] if len(parts) > 1 else ""
    if not task:
        return CommandResult(text="Please provide a task for the agent.", success=False)

    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available", success=False)

    coordinator = _get_coordinator(brain)
    if coordinator:
        handle = await coordinator.spawn_agent(brain.reasoner, agent_type, task)
        return CommandResult(text=f"Agent spawned: {handle['id']} ({agent_type})\nTask: {task}\nStatus: running")

    # Fallback: run directly through agent loop
    from brain.agent.loop import _run_sub_agent
    result = await _run_sub_agent(brain.reasoner, agent_type, task)
    return CommandResult(text=result)


@command("agents", description="List all available agent types",
         usage="/agents", category="agent", permission=PermLevel.READ_ONLY)
async def cmd_agents(ctx: CommandContext) -> CommandResult:
    lines = [
        "Available Agent Types",
        "=" * 40,
        "  scout     Read-only exploration — find files, read code, search",
        "  worker    Full access execution — edit, install, build, run",
        "  planner   Analysis only — research, plan, no execution",
        "",
        "System Agents (via /delegate):",
        "  terminal   Raw shell access",
        "  network    Network operations & scanning",
        "  security   Pentesting & vulnerability analysis",
        "  file       File system operations",
        "  desktop    GUI & desktop control",
        "  app        Application management",
        "  system     Package & service management",
        "  vision     Screenshot analysis",
        "  research   Web research & information gathering",
    ]
    brain = ctx.brain
    coordinator = _get_coordinator(brain)
    if coordinator:
        running = coordinator.list_running()
        if running:
            lines.append(f"\nRunning Agents ({len(running)}):")
            for a in running:
                lines.append(f"  [{a['id'][:8]}] {a['type']} — {a['task'][:50]}")
    return CommandResult(text="\n".join(lines))


@command("team", description="Create a team of agents for a complex task",
         usage='/team "<goal>" <type1,type2,...>', category="agent", permission=PermLevel.FULL)
async def cmd_team(ctx: CommandContext) -> CommandResult:
    args = ctx.args.strip()
    if not args:
        return CommandResult(
            text='Usage: /team "build a REST API" scout,planner,worker',
            success=False,
        )

    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available", success=False)

    # Parse: quoted goal then comma-separated agent types
    import shlex
    try:
        tokens = shlex.split(args)
    except ValueError:
        return CommandResult(text="Could not parse arguments. Quote the goal string.", success=False)

    if len(tokens) < 2:
        return CommandResult(text='Usage: /team "goal description" scout,planner,worker', success=False)

    goal = tokens[0]
    agent_types = [t.strip().lower() for t in tokens[1].split(",") if t.strip()]

    coordinator = _get_coordinator(brain)
    if coordinator:
        handles = []
        for atype in agent_types:
            h = await coordinator.spawn_agent(brain.reasoner, atype, goal)
            handles.append(h)
        ids = ", ".join(h['id'][:8] for h in handles)
        return CommandResult(
            text=f"Team created with {len(handles)} agents: [{ids}]\nGoal: {goal}\nTypes: {', '.join(agent_types)}"
        )

    # Fallback: sequential execution
    from brain.agent.loop import _run_sub_agent
    results = []
    for atype in agent_types:
        r = await _run_sub_agent(brain.reasoner, atype, goal)
        results.append(f"[{atype}] {r}")
    return CommandResult(text="\n\n".join(results))


@command("scout", description="Spawn a read-only scout agent",
         usage="/scout <task>", category="agent", permission=PermLevel.STANDARD)
async def cmd_scout(ctx: CommandContext) -> CommandResult:
    return await _spawn_shortcut(ctx, "scout")


@command("worker", description="Spawn a full-access worker agent",
         usage="/worker <task>", category="agent", permission=PermLevel.FULL)
async def cmd_worker(ctx: CommandContext) -> CommandResult:
    return await _spawn_shortcut(ctx, "worker")


@command("planner", description="Spawn an analysis-only planner agent",
         usage="/planner <task>", category="agent", permission=PermLevel.STANDARD)
async def cmd_planner(ctx: CommandContext) -> CommandResult:
    return await _spawn_shortcut(ctx, "planner")


@command("orchestrate", aliases=["orch"], description="Run a multi-step workflow using pipeline strategy",
         usage="/orchestrate <workflow description>", category="agent", permission=PermLevel.FULL)
async def cmd_orchestrate(ctx: CommandContext) -> CommandResult:
    task = ctx.args.strip()
    if not task:
        return CommandResult(text="Usage: /orchestrate <workflow description>", success=False)

    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available", success=False)

    coordinator = _get_coordinator(brain)
    if coordinator:
        result = await coordinator.orchestrate(brain.reasoner, task, strategy="pipeline")
        return CommandResult(
            text=f"Orchestration complete.\n\n{result.get('summary', 'Done.')}",
            data=result,
        )

    # Fallback: plan then execute
    from brain.agent.loop import _run_sub_agent
    plan = await _run_sub_agent(brain.reasoner, "planner", f"Create a step-by-step plan for: {task}")
    execution = await _run_sub_agent(brain.reasoner, "worker", f"Execute this plan:\n{plan}")
    return CommandResult(text=f"Plan:\n{plan}\n\nExecution:\n{execution}")


@command("coordinate", aliases=["coord"], description="Run parallel agents on related subtasks",
         usage="/coordinate <task description>", category="agent", permission=PermLevel.FULL)
async def cmd_coordinate(ctx: CommandContext) -> CommandResult:
    task = ctx.args.strip()
    if not task:
        return CommandResult(text="Usage: /coordinate <task description>", success=False)

    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available", success=False)

    coordinator = _get_coordinator(brain)
    if coordinator:
        result = await coordinator.orchestrate(brain.reasoner, task, strategy="parallel")
        return CommandResult(
            text=f"Coordination complete.\n\n{result.get('summary', 'Done.')}",
            data=result,
        )

    # Fallback: scout + planner in parallel, then worker
    from brain.agent.loop import _run_sub_agent
    scout_task = asyncio.create_task(_run_sub_agent(brain.reasoner, "scout", task))
    planner_task = asyncio.create_task(_run_sub_agent(brain.reasoner, "planner", task))
    scout_result, planner_result = await asyncio.gather(scout_task, planner_task)
    combined = f"Scout findings:\n{scout_result}\n\nPlanner output:\n{planner_result}"
    execution = await _run_sub_agent(brain.reasoner, "worker", f"Using this context:\n{combined}\n\nExecute: {task}")
    return CommandResult(text=f"{combined}\n\nExecution:\n{execution}")


@command("delegate", description="Hand task to a specialist system agent",
         usage="/delegate <agent_type> <task>", category="agent", permission=PermLevel.FULL)
async def cmd_delegate(ctx: CommandContext) -> CommandResult:
    args = ctx.args.strip()
    if not args:
        return CommandResult(
            text="Usage: /delegate <terminal|network|security|file|desktop|app|system|vision|research> <task>",
            success=False,
        )

    parts = args.split(None, 1)
    agent_type = parts[0].lower()
    task = parts[1] if len(parts) > 1 else ""
    if not task:
        return CommandResult(text="Please provide a task for the delegate.", success=False)

    valid_delegates = {
        "terminal", "network", "security", "file", "desktop",
        "app", "system", "vision", "research",
    }
    if agent_type not in valid_delegates:
        return CommandResult(
            text=f"Unknown delegate type: {agent_type}\nAvailable: {', '.join(sorted(valid_delegates))}",
            success=False,
        )

    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available", success=False)

    coordinator = _get_coordinator(brain)
    if coordinator:
        handle = await coordinator.spawn_agent(brain.reasoner, agent_type, task)
        result = await coordinator.wait_for(handle['id'])
        return CommandResult(text=f"[{agent_type}] {result}")

    from brain.agent.loop import _run_sub_agent
    result = await _run_sub_agent(brain.reasoner, agent_type, task)
    return CommandResult(text=f"[{agent_type}] {result}")


@command("spawn", description="Spawn a background agent (non-blocking)",
         usage="/spawn <type> <task>", category="agent", permission=PermLevel.FULL)
async def cmd_spawn(ctx: CommandContext) -> CommandResult:
    args = ctx.args.strip()
    if not args:
        return CommandResult(text="Usage: /spawn <type> <task>", success=False)

    parts = args.split(None, 1)
    agent_type = parts[0].lower()
    task = parts[1] if len(parts) > 1 else ""
    if not task:
        return CommandResult(text="Please provide a task.", success=False)

    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available", success=False)

    coordinator = _get_coordinator(brain)
    if coordinator:
        handle = await coordinator.spawn_agent(brain.reasoner, agent_type, task, background=True)
        return CommandResult(
            text=f"Background agent spawned: {handle['id'][:8]} ({agent_type})\nTask: {task}\nUse /agent-status to check progress.",
        )

    # Fallback: fire-and-forget via asyncio
    from brain.agent.loop import _run_sub_agent
    bg_task = asyncio.create_task(_run_sub_agent(brain.reasoner, agent_type, task))
    task_id = id(bg_task)
    if not hasattr(brain, '_background_tasks'):
        brain._background_tasks = {}
    brain._background_tasks[str(task_id)] = {"task": bg_task, "type": agent_type, "desc": task}
    return CommandResult(
        text=f"Background agent spawned (fallback): {task_id}\nType: {agent_type}\nTask: {task}",
    )


@command("kill-agent", aliases=["ka"], description="Stop a running agent by ID",
         usage="/kill-agent <agent_id>", category="agent", permission=PermLevel.FULL)
async def cmd_kill_agent(ctx: CommandContext) -> CommandResult:
    agent_id = ctx.args.strip()
    if not agent_id:
        return CommandResult(text="Usage: /kill-agent <agent_id>", success=False)

    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available", success=False)

    coordinator = _get_coordinator(brain)
    if coordinator:
        success = await coordinator.kill_agent(agent_id)
        if success:
            return CommandResult(text=f"Agent {agent_id} terminated.")
        return CommandResult(text=f"Agent {agent_id} not found or already finished.", success=False)

    # Fallback: check _background_tasks
    bg = getattr(brain, '_background_tasks', {})
    if agent_id in bg:
        bg[agent_id]['task'].cancel()
        del bg[agent_id]
        return CommandResult(text=f"Background task {agent_id} cancelled.")
    return CommandResult(text=f"Agent {agent_id} not found.", success=False)


@command("agent-status", aliases=["as"], description="Show status of all running agents",
         usage="/agent-status [agent_id]", category="agent", permission=PermLevel.READ_ONLY)
async def cmd_agent_status(ctx: CommandContext) -> CommandResult:
    target = ctx.args.strip()
    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available", success=False)

    coordinator = _get_coordinator(brain)
    if coordinator:
        if target:
            status = coordinator.get_status(target)
            if status:
                return CommandResult(text=(
                    f"Agent: {status['id']}\n"
                    f"Type:  {status['type']}\n"
                    f"State: {status['state']}\n"
                    f"Task:  {status['task']}\n"
                    f"Steps: {status.get('steps', 'N/A')}"
                ))
            return CommandResult(text=f"Agent {target} not found.", success=False)

        running = coordinator.list_running()
        if not running:
            return CommandResult(text="No agents currently running.")
        lines = [f"Running Agents ({len(running)})", "=" * 40]
        for a in running:
            lines.append(f"  [{a['id'][:8]}] {a['type']:<10s} {a['state']:<10s} {a['task'][:40]}")
        return CommandResult(text="\n".join(lines))

    # Fallback: check _background_tasks
    bg = getattr(brain, '_background_tasks', {})
    if not bg:
        return CommandResult(text="No background agents running.")
    lines = [f"Background Tasks ({len(bg)})", "=" * 40]
    for tid, info in bg.items():
        state = "done" if info['task'].done() else "running"
        lines.append(f"  [{tid}] {info['type']:<10s} {state:<10s} {info['desc'][:40]}")
    return CommandResult(text="\n".join(lines))
