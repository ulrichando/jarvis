"""Agent & Team commands -- spawn, coordinate, and manage agents."""
import asyncio
import logging
import time

from src.commands.registry import command, CommandContext, CommandResult, PermLevel

log = logging.getLogger("jarvis.commands.agent")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_coordinator(brain):
    """Return the brain's AgentCoordinator if available."""
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
        return CommandResult(text="Brain not available.", success=False)

    coordinator = _get_coordinator(brain)
    if coordinator:
        handle = coordinator.spawn_agent(brain.reasoner, agent_type, task)
        return CommandResult(
            text=f"Agent spawned: {handle.id} ({agent_type})\n"
                 f"  Task:   {task}\n"
                 f"  Status: running\n"
                 f"  Use /agent-status {handle.id} to check progress."
        )

    from src.agent.loop import _run_sub_agent
    result = await _run_sub_agent(brain.reasoner, agent_type, task)
    return CommandResult(text=result)


# ---------------------------------------------------------------------------
# /agent -- Generic agent command
# ---------------------------------------------------------------------------

@command("agent", description="Spawn a named agent with a task",
         usage="/agent <type> <task>", category="agent", permission=PermLevel.FULL)
async def cmd_agent(ctx: CommandContext) -> CommandResult:
    args = ctx.args.strip()
    if not args:
        try:
            from src.agent.agents import get_all_agent_names
            available = ", ".join(get_all_agent_names())
        except ImportError:
            available = "scout, worker, planner"
        return CommandResult(text=f"Usage: /agent <type> <task>\nAvailable: {available}", success=False)

    parts = args.split(None, 1)
    agent_type = parts[0].lower()
    task = parts[1] if len(parts) > 1 else ""
    if not task:
        return CommandResult(text="Please provide a task for the agent.", success=False)

    # Validate agent type exists
    try:
        from src.agent.agents import resolve_agent, get_all_agent_names
        if not resolve_agent(agent_type):
            available = ", ".join(get_all_agent_names())
            return CommandResult(text=f"Unknown agent: {agent_type}\nAvailable: {available}", success=False)
    except ImportError:
        pass  # Proceed anyway if agents module not available

    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available.", success=False)

    coordinator = _get_coordinator(brain)
    if coordinator:
        handle = coordinator.spawn_agent(brain.reasoner, agent_type, task)
        return CommandResult(
            text=f"Agent spawned: {handle.id} ({agent_type})\n"
                 f"  Task:   {task}\n"
                 f"  Status: {handle.status}\n"
                 f"  Use /agent-status {handle.id} to check progress."
        )

    # Fallback: run directly through agent loop
    from src.agent.loop import _run_sub_agent
    result = await _run_sub_agent(brain.reasoner, agent_type, task)
    return CommandResult(text=result)


# ---------------------------------------------------------------------------
# /agents -- List, create, manage agents
# ---------------------------------------------------------------------------

@command("agents", description="List, create, and manage agents",
         usage="/agents [list|create|generate|info|delete|reload] [args]",
         category="agent", permission=PermLevel.STANDARD)
async def cmd_agents(ctx: CommandContext) -> CommandResult:
    args = ctx.args.strip().split(None, 1)
    subcommand = args[0].lower() if args else "list"
    sub_args = args[1].strip() if len(args) > 1 else ""

    brain = ctx.brain

    if subcommand == "list":
        return await _agents_list(brain)
    elif subcommand in ("create", "new", "add"):
        return await _agents_create(brain, sub_args)
    elif subcommand in ("generate", "gen"):
        return await _agents_generate(brain, sub_args)
    elif subcommand == "info":
        return await _agents_info(sub_args)
    elif subcommand in ("delete", "rm", "remove"):
        return await _agents_delete(sub_args)
    elif subcommand == "reload":
        return await _agents_reload()
    else:
        # Treat unknown subcommand as "info <name>"
        return await _agents_info(subcommand)


async def _agents_list(brain) -> CommandResult:
    """List all available agents (built-in + custom)."""
    try:
        from src.agent.agents import list_all_agents
        agents = list_all_agents()
    except ImportError:
        agents = []

    lines = [
        "+--------------------------------------------------+",
        "|            Available Agents                       |",
        "+--------------------------------------------------+",
    ]

    # Built-in
    builtin = [a for a in agents if a.get("type") == "built-in"]
    if builtin:
        lines.append("\n  Built-in:")
        for a in builtin:
            tools = a.get("tools", [])
            max_iter = a.get("max_iterations", "N/A")
            lines.append(f"    {a['name']:<12s} {a['description']}")
            lines.append(f"      Tools: {', '.join(tools) if tools else 'full'}  |  Max iters: {max_iter}")

    # Custom
    custom = [a for a in agents if a.get("type") == "custom"]
    if custom:
        lines.append("\n  Custom:")
        for a in custom:
            scope_tag = f"[{a['scope']}]" if a.get("scope") else ""
            model_tag = f" ({a['model']})" if a.get("model") else ""
            lines.append(f"    {a['name']:<12s} {a['description']}{model_tag} {scope_tag}")

    # System delegates
    lines.append("\n  System (via /delegate):")
    for name in ["terminal", "network", "security", "file", "desktop",
                 "app", "system", "vision", "research"]:
        lines.append(f"    {name}")

    # Running agents
    coordinator = _get_coordinator(brain)
    if coordinator:
        running = coordinator.list_running()
        if running:
            lines.append(f"\n  Running ({len(running)}):")
            for a in running:
                lines.append(f"    [{a['id'][:8]}] {a['type']} -- {a['task'][:50]}")

    lines.append("")
    lines.append(f"  Total: {len(builtin)} built-in, {len(custom)} custom")
    lines.append("  Commands: /agents create | /agents generate <desc>")
    lines.append("            /agents info <name> | /agents delete <name>")
    lines.append("            /agents reload")

    return CommandResult(text="\n".join(lines))


async def _agents_create(brain, args: str) -> CommandResult:
    """Create an agent manually via arguments."""
    if not args:
        return CommandResult(text=(
            "Usage: /agents create <name> [options]\n\n"
            "Options:\n"
            "  --scope user|project    Where to save (default: user)\n"
            "  --tools t1,t2,...       Allowed tools (default: full access)\n"
            "  --model <model>         Model preference\n"
            "  --readonly              Enforce read-only bash\n"
            "  --max-iters <N>         Max iterations (default: 15)\n"
            "  --desc <description>    One-line description\n"
            "  --prompt <text>         System prompt (or provide on next line)\n\n"
            "Or use: /agents generate <description> -- to have the LLM create one for you"
        ), success=False)

    import shlex
    try:
        tokens = shlex.split(args)
    except ValueError:
        return CommandResult(text="Could not parse arguments.", success=False)

    name = tokens[0]
    scope = "user"
    tools = None
    model = ""
    readonly = False
    max_iters = 999
    description = ""
    prompt = ""

    i = 1
    while i < len(tokens):
        if tokens[i] == "--scope" and i + 1 < len(tokens):
            scope = tokens[i + 1]
            i += 2
        elif tokens[i] == "--tools" and i + 1 < len(tokens):
            tools = [t.strip() for t in tokens[i + 1].split(",")]
            i += 2
        elif tokens[i] == "--model" and i + 1 < len(tokens):
            model = tokens[i + 1]
            i += 2
        elif tokens[i] == "--readonly":
            readonly = True
            i += 1
        elif tokens[i] == "--max-iters" and i + 1 < len(tokens):
            try:
                max_iters = int(tokens[i + 1])
            except ValueError:
                return CommandResult(text=f"Invalid --max-iters value: {tokens[i + 1]}", success=False)
            i += 2
        elif tokens[i] == "--desc" and i + 1 < len(tokens):
            description = tokens[i + 1]
            i += 2
        elif tokens[i] == "--prompt" and i + 1 < len(tokens):
            prompt = tokens[i + 1]
            i += 2
        else:
            i += 1

    if not description:
        description = f"Custom {name} agent"
    if not prompt:
        prompt = (
            f"You are a JARVIS {name} agent.\n\n"
            f"Your job is to assist with tasks related to: {description}\n\n"
            f"Be thorough, precise, and efficient."
        )

    try:
        from src.agent.registry import AgentRegistry
    except ImportError:
        return CommandResult(text="Agent registry module not available.", success=False)

    registry = AgentRegistry()
    registry.discover()

    if registry.exists(name):
        return CommandResult(
            text=f"Agent '{name}' already exists. Delete it first or choose a different name.",
            success=False,
        )

    agent = registry.create_agent(
        name=name,
        description=description,
        system_prompt=prompt,
        allowed_tools=tools,
        max_iterations=max_iters,
        model=model,
        scope=scope,
        bash_readonly=readonly,
    )

    # Reload the global registry
    try:
        from src.agent.agents import reload_registry
        reload_registry()
    except ImportError:
        pass

    tools_str = ", ".join(agent.allowed_tools) if agent.allowed_tools else "full access"
    return CommandResult(
        text=(
            f"Agent created: {agent.name}\n"
            f"  Description: {agent.description}\n"
            f"  Scope:       {agent.scope} ({agent.path})\n"
            f"  Tools:       {tools_str}\n"
            f"  Iterations:  {agent.max_iterations}\n"
            f"  Bash R/O:    {agent.bash_readonly}\n\n"
            f"Use it: /agent {agent.name.lower()} <task>"
        ),
    )


async def _agents_generate(brain, description: str) -> CommandResult:
    """Generate a new agent using the LLM based on a description."""
    if not description:
        return CommandResult(
            text="Usage: /agents generate <description of what the agent should do>",
            success=False,
        )

    if not brain or not hasattr(brain, 'reasoner'):
        return CommandResult(text="Brain/reasoner not available for generation.", success=False)

    try:
        from src.agent.registry import AgentRegistry
    except ImportError:
        return CommandResult(text="Agent registry module not available.", success=False)

    registry = AgentRegistry()
    registry.discover()

    # Build generation prompt and query LLM
    gen_prompt = registry.build_generation_prompt(description)

    try:
        response = await brain.reasoner.query(gen_prompt)
        if not response:
            return CommandResult(text="LLM returned empty response.", success=False)
    except Exception as e:
        return CommandResult(text=f"LLM query failed: {e}", success=False)

    # Parse the generated config
    parsed = registry.parse_generated_agent(response)
    if not parsed:
        return CommandResult(
            text=f"Failed to parse LLM output. Raw response:\n{response[:500]}",
            success=False,
        )

    # Check for duplicates
    if registry.exists(parsed["name"]):
        return CommandResult(
            text=f"Agent '{parsed['name']}' already exists. Delete it first.",
            success=False,
        )

    # Create the agent
    agent = registry.create_agent(
        name=parsed["name"],
        description=parsed.get("description", description),
        system_prompt=parsed["prompt"],
        allowed_tools=parsed.get("tools"),
        max_iterations=parsed.get("max_iterations", 999),
        model=parsed.get("model", ""),
        scope="user",
        bash_readonly=parsed.get("bash_readonly", False),
    )

    # Reload the global registry
    try:
        from src.agent.agents import reload_registry
        reload_registry()
    except ImportError:
        pass

    tools_str = ", ".join(agent.allowed_tools) if agent.allowed_tools else "full access"
    return CommandResult(
        text=(
            f"Agent generated and saved!\n\n"
            f"  Name:        {agent.name}\n"
            f"  Description: {agent.description}\n"
            f"  Tools:       {tools_str}\n"
            f"  Iterations:  {agent.max_iterations}\n"
            f"  Saved to:    {agent.path}\n\n"
            f"Use it: /agent {agent.name.lower()} <task>\n"
            f"Edit:   {agent.path}"
        ),
    )


async def _agents_info(name: str) -> CommandResult:
    """Show detailed info about a specific agent."""
    if not name:
        return CommandResult(text="Usage: /agents info <agent_name>", success=False)

    try:
        from src.agent.agents import AGENT_CONFIGS
    except ImportError:
        return CommandResult(text="Agent configs not available.", success=False)

    # Check built-in
    builtin = AGENT_CONFIGS.get(name.lower())
    if builtin:
        return CommandResult(text=(
            f"Agent: {builtin.name}\n"
            f"  Type:        built-in\n"
            f"  Description: {builtin.description}\n"
            f"  Tools:       {', '.join(builtin.allowed_tools)}\n"
            f"  Max Iters:   {builtin.max_iterations}\n\n"
            f"System Prompt:\n{builtin.system_prompt[:500]}"
        ))

    # Check custom
    try:
        from src.agent.registry import AgentRegistry
        registry = AgentRegistry()
        registry.discover()
        custom = registry.get(name)
        if custom:
            return CommandResult(text=(
                f"Agent: {custom.name}\n"
                f"  Type:        custom ({custom.scope})\n"
                f"  Description: {custom.description}\n"
                f"  Tools:       {', '.join(custom.allowed_tools) if custom.allowed_tools else 'full access'}\n"
                f"  Model:       {custom.model or 'default'}\n"
                f"  Max Iters:   {custom.max_iterations}\n"
                f"  Bash:        {'read-only' if custom.bash_readonly else 'full'}\n"
                f"  Path:        {custom.path}\n\n"
                f"System Prompt:\n{custom.system_prompt[:800]}"
            ))
    except ImportError:
        pass

    return CommandResult(
        text=f"Agent '{name}' not found. Use /agents list to see available agents.",
        success=False,
    )


async def _agents_delete(name: str) -> CommandResult:
    """Delete a custom agent."""
    if not name:
        return CommandResult(text="Usage: /agents delete <agent_name>", success=False)

    try:
        from src.agent.agents import AGENT_CONFIGS
        if name.lower() in AGENT_CONFIGS:
            return CommandResult(text=f"Cannot delete built-in agent '{name}'.", success=False)
    except ImportError:
        pass

    try:
        from src.agent.registry import AgentRegistry
        registry = AgentRegistry()
        registry.discover()
        if registry.delete_agent(name):
            try:
                from src.agent.agents import reload_registry
                reload_registry()
            except ImportError:
                pass
            return CommandResult(text=f"Agent '{name}' deleted.")
    except ImportError:
        return CommandResult(text="Agent registry module not available.", success=False)

    return CommandResult(text=f"Agent '{name}' not found or could not be deleted.", success=False)


async def _agents_reload() -> CommandResult:
    """Reload agent registry from disk."""
    try:
        from src.agent.agents import reload_registry
        count = reload_registry()
        return CommandResult(text=f"Agent registry reloaded. Found {count} custom agent(s).")
    except ImportError:
        return CommandResult(text="Agent registry module not available.", success=False)


# ---------------------------------------------------------------------------
# /team -- Team management
# ---------------------------------------------------------------------------

@command("team", description="Team management: status or spawn a team for a goal",
         usage="/team <status | spawn <name> | \"goal\" type1,type2,...>",
         category="agent", permission=PermLevel.FULL)
async def cmd_team(ctx: CommandContext) -> CommandResult:
    args = ctx.args.strip()
    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available.", success=False)

    if not hasattr(brain, '_teams'):
        brain._teams = []

    if not args:
        return CommandResult(
            text="Usage:\n"
                 '  /team "goal" scout,planner,worker   Create a team\n'
                 "  /team status                        Show team status\n"
                 "  /team spawn <name>                  Spawn a named team config",
            success=False,
        )

    # /team status
    if args.lower() == "status":
        if not brain._teams:
            return CommandResult(text="No teams created yet.")
        lines = [f"Teams ({len(brain._teams)})", "=" * 40]
        for i, team in enumerate(brain._teams):
            lines.append(f"\n  Team {i + 1}: {team['goal'][:60]}")
            lines.append(f"    Types:  {', '.join(team['types'])}")
            lines.append(f"    Status: {team.get('status', 'running')}")
            if team.get('agent_ids'):
                lines.append(f"    Agents: {', '.join(team['agent_ids'])}")
        return CommandResult(text="\n".join(lines))

    # /team spawn <name>
    parts = args.split(None, 1)
    if parts[0].lower() == "spawn":
        name = parts[1] if len(parts) > 1 else ""
        if not name:
            return CommandResult(text="Usage: /team spawn <team_name>", success=False)
        # Spawn a default team configuration
        agent_types = ["scout", "planner", "worker"]
        coordinator = _get_coordinator(brain)
        if coordinator:
            handles = []
            for atype in agent_types:
                h = coordinator.spawn_agent(brain.reasoner, atype, f"Team '{name}' task")
                handles.append(h)
            team_record = {
                "goal": name,
                "types": agent_types,
                "agent_ids": [h.id for h in handles],
                "status": "running",
                "created_at": time.time(),
            }
            brain._teams.append(team_record)
            ids = ", ".join(h.id[:8] for h in handles)
            return CommandResult(
                text=f"Team '{name}' spawned with {len(handles)} agents: [{ids}]\n"
                     f"  Types: {', '.join(agent_types)}\n"
                     f"  Use /team status to monitor."
            )
        return CommandResult(text=f"Team '{name}' registered (no coordinator -- agents will run sequentially).")

    # /team "goal" type1,type2,...
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
            h = coordinator.spawn_agent(brain.reasoner, atype, goal)
            handles.append(h)
        team_record = {
            "goal": goal,
            "types": agent_types,
            "agent_ids": [h.id for h in handles],
            "status": "running",
            "created_at": time.time(),
        }
        brain._teams.append(team_record)
        ids = ", ".join(h.id[:8] for h in handles)
        return CommandResult(
            text=f"Team created with {len(handles)} agents: [{ids}]\n"
                 f"  Goal:  {goal}\n"
                 f"  Types: {', '.join(agent_types)}"
        )

    # Fallback: sequential execution
    from src.agent.loop import _run_sub_agent
    results = []
    team_record = {
        "goal": goal, "types": agent_types, "agent_ids": [], "status": "done",
        "created_at": time.time(),
    }
    brain._teams.append(team_record)
    for atype in agent_types:
        r = await _run_sub_agent(brain.reasoner, atype, goal)
        results.append(f"[{atype}] {r}")
    return CommandResult(text="\n\n".join(results))


# ---------------------------------------------------------------------------
# /scout -- Spawn a read-only scout agent
# ---------------------------------------------------------------------------

@command("scout", description="Spawn a read-only scout agent",
         usage="/scout <task>", category="agent", permission=PermLevel.STANDARD)
async def cmd_scout(ctx: CommandContext) -> CommandResult:
    return await _spawn_shortcut(ctx, "scout")


# ---------------------------------------------------------------------------
# /worker -- Spawn a full-access worker agent
# ---------------------------------------------------------------------------

@command("worker", description="Spawn a full-access worker agent",
         usage="/worker <task>", category="agent", permission=PermLevel.FULL)
async def cmd_worker(ctx: CommandContext) -> CommandResult:
    return await _spawn_shortcut(ctx, "worker")


# ---------------------------------------------------------------------------
# /planner -- Spawn an analysis-only planner agent
# ---------------------------------------------------------------------------

@command("planner", description="Spawn an analysis-only planner agent",
         usage="/planner <task>", category="agent", permission=PermLevel.STANDARD)
async def cmd_planner(ctx: CommandContext) -> CommandResult:
    return await _spawn_shortcut(ctx, "planner")


# ---------------------------------------------------------------------------
# /orchestrate -- Multi-agent orchestration with pipeline strategy
# ---------------------------------------------------------------------------

@command("orchestrate", aliases=["orch"], description="Multi-agent pipeline: research → plan → implement → verify",
         usage="/orchestrate <goal>", category="agent", permission=PermLevel.FULL)
async def cmd_orchestrate(ctx: CommandContext) -> CommandResult:
    task = ctx.args.strip()
    if not task:
        return CommandResult(text="Usage: /orchestrate <goal>", success=False)

    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available.", success=False)

    # Use the enhanced coordinator's pipeline execution
    enhanced = getattr(brain, '_coordinator_enhanced', None)
    if enhanced:
        try:
            result = await enhanced.execute_pipeline(goal=task, timeout=300)
            return CommandResult(
                text=f"Pipeline complete.\n\n{result}",
                data={"result": result},
            )
        except Exception as e:
            log.warning("Enhanced pipeline failed: %s, using fallback", e)

    # Fallback: plan then execute
    from src.agent.loop import _run_sub_agent
    try:
        plan = await _run_sub_agent(brain.reasoner, "planner", f"Create a step-by-step plan for: {task}")
        execution = await _run_sub_agent(brain.reasoner, "worker", f"Execute this plan:\n{plan}")
    except Exception as e:
        return CommandResult(text=f"Orchestration failed: {e}", success=False)

    return CommandResult(text=f"Plan:\n{plan}\n\nExecution:\n{execution}")


# ---------------------------------------------------------------------------
# /coordinate -- Run parallel agents on related subtasks
# ---------------------------------------------------------------------------

@command("coordinate", aliases=["coord"], description="Decompose task into parallel subtasks via swarm",
         usage="/coordinate <goal>", category="agent", permission=PermLevel.FULL)
async def cmd_coordinate(ctx: CommandContext) -> CommandResult:
    task = ctx.args.strip()
    if not task:
        return CommandResult(text="Usage: /coordinate <goal>", success=False)

    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available.", success=False)

    # Use coordinator's swarm mode (thread-based parallel decomposition)
    coordinator = _get_coordinator(brain)
    if coordinator:
        try:
            result = await coordinator.swarm(brain.reasoner, task, timeout=180)
            return CommandResult(
                text=f"Coordination complete.\n\n{result}",
                data={"result": result},
            )
        except Exception as e:
            log.warning("Coordinator swarm failed: %s, using fallback", e)

    # Fallback: scout + planner in parallel, then worker
    from src.agent.loop import _run_sub_agent
    try:
        scout_coro = _run_sub_agent(brain.reasoner, "scout", task)
        planner_coro = _run_sub_agent(brain.reasoner, "planner", task)
        scout_result, planner_result = await asyncio.gather(
            asyncio.create_task(scout_coro),
            asyncio.create_task(planner_coro),
        )
        combined = f"Scout findings:\n{scout_result}\n\nPlanner output:\n{planner_result}"
        execution = await _run_sub_agent(
            brain.reasoner, "worker",
            f"Using this context:\n{combined}\n\nExecute: {task}",
        )
    except Exception as e:
        return CommandResult(text=f"Coordination failed: {e}", success=False)

    return CommandResult(text=f"{combined}\n\nExecution:\n{execution}")


# ---------------------------------------------------------------------------
# /swarm -- Async agent swarm (Kimi K2.5-inspired parallel execution)
# ---------------------------------------------------------------------------

@command("swarm", description="Async agent swarm: decompose task, spawn specialist agents in parallel, aggregate",
         usage="/swarm <task>", category="agent", permission=PermLevel.FULL)
async def cmd_swarm(ctx: CommandContext) -> CommandResult:
    task = ctx.args.strip()
    if not task:
        return CommandResult(text="Usage: /swarm <task>", success=False)

    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available.", success=False)

    swarm = getattr(brain, 'swarm', None)
    if not swarm:
        return CommandResult(text="Swarm not available.", success=False)

    try:
        result = await swarm.run(task, max_agents=5, timeout=120)
        return CommandResult(
            text=f"Swarm complete.\n\n{result}",
            data={"result": result},
        )
    except Exception as e:
        return CommandResult(text=f"Swarm failed: {e}", success=False)


# ---------------------------------------------------------------------------
# /delegate -- Hand task to a specialist system agent
# ---------------------------------------------------------------------------

@command("delegate", description="Delegate task to a specialist agent",
         usage="/delegate <agent_type> <task>", category="agent", permission=PermLevel.FULL)
async def cmd_delegate(ctx: CommandContext) -> CommandResult:
    args = ctx.args.strip()
    if not args:
        return CommandResult(
            text="Usage: /delegate <type> <task>\n\n"
                 "Available types:\n"
                 "  terminal   -- Shell/terminal tasks\n"
                 "  network    -- Network analysis and requests\n"
                 "  security   -- Security scanning and audits\n"
                 "  file       -- File management and organization\n"
                 "  desktop    -- Desktop automation\n"
                 "  app        -- Application management\n"
                 "  system     -- System administration\n"
                 "  vision     -- Image/screen analysis\n"
                 "  research   -- Web research and information gathering",
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
        return CommandResult(text="Brain not available.", success=False)

    coordinator = _get_coordinator(brain)
    if coordinator:
        try:
            handle = coordinator.spawn_agent(brain.reasoner, agent_type, task)
            result_handle = coordinator.wait_for(handle.id)
            if result_handle and result_handle.result:
                return CommandResult(text=f"[{agent_type}] {result_handle.result}")
            elif result_handle and result_handle.error:
                return CommandResult(text=f"[{agent_type}] Error: {result_handle.error}", success=False)
            return CommandResult(text=f"[{agent_type}] Completed (no output).")
        except Exception as e:
            return CommandResult(text=f"[{agent_type}] Delegation failed: {e}", success=False)

    from src.agent.loop import _run_sub_agent
    try:
        result = await _run_sub_agent(brain.reasoner, agent_type, task)
    except Exception as e:
        return CommandResult(text=f"[{agent_type}] Delegation failed: {e}", success=False)
    return CommandResult(text=f"[{agent_type}] {result}")


# ---------------------------------------------------------------------------
# /spawn -- Spawn a background agent (non-blocking)
# ---------------------------------------------------------------------------

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
        return CommandResult(text="Brain not available.", success=False)

    coordinator = _get_coordinator(brain)
    if coordinator:
        handle = coordinator.spawn_agent(brain.reasoner, agent_type, task)
        return CommandResult(
            text=f"Background agent spawned: {handle.id} ({agent_type})\n"
                 f"  Task:   {task}\n"
                 f"  Status: {handle.status}\n"
                 f"  Use /agent-status {handle.id} to check progress.",
        )

    # Fallback: fire-and-forget via asyncio
    from src.agent.loop import _run_sub_agent
    bg_task = asyncio.create_task(_run_sub_agent(brain.reasoner, agent_type, task))
    task_id = str(id(bg_task))
    if not hasattr(brain, '_background_tasks'):
        brain._background_tasks = {}
    brain._background_tasks[task_id] = {"task": bg_task, "type": agent_type, "desc": task}
    return CommandResult(
        text=f"Background agent spawned: {task_id}\n"
             f"  Type:   {agent_type}\n"
             f"  Task:   {task}\n"
             f"  Use /agent-status to check progress.",
    )


# ---------------------------------------------------------------------------
# /kill-agent -- Stop a running agent by ID
# ---------------------------------------------------------------------------

@command("kill-agent", aliases=["ka"], description="Kill a running agent by ID",
         usage="/kill-agent <agent_id>", category="agent", permission=PermLevel.FULL)
async def cmd_kill_agent(ctx: CommandContext) -> CommandResult:
    agent_id = ctx.args.strip()
    if not agent_id:
        # Show running agents so user can pick one
        brain = ctx.brain
        coordinator = _get_coordinator(brain) if brain else None
        if coordinator:
            running = coordinator.list_running()
            if running:
                lines = ["Running agents (provide an ID to kill):"]
                for a in running:
                    lines.append(f"  {a['id']}  {a['type']:<10s}  {a['task'][:40]}")
                return CommandResult(text="\n".join(lines), success=False)
        return CommandResult(text="Usage: /kill-agent <agent_id>", success=False)

    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available.", success=False)

    coordinator = _get_coordinator(brain)
    if coordinator:
        success = coordinator.kill_agent(agent_id)
        if success:
            return CommandResult(text=f"Agent {agent_id} terminated.")
        return CommandResult(text=f"Agent {agent_id} not found or already finished.", success=False)

    # Fallback: check _background_tasks
    bg = getattr(brain, '_background_tasks', {})
    # Support partial ID matching
    matches = [k for k in bg if k.startswith(agent_id)]
    if len(matches) == 1:
        key = matches[0]
        bg[key]['task'].cancel()
        info = bg.pop(key)
        return CommandResult(text=f"Background task {key} cancelled.\n  Was: {info['desc']}")
    elif len(matches) > 1:
        return CommandResult(
            text=f"Ambiguous ID '{agent_id}'. Matches: {', '.join(matches)}",
            success=False,
        )

    return CommandResult(text=f"Agent {agent_id} not found.", success=False)


# ---------------------------------------------------------------------------
# /agent-status -- Show status of all running agents
# ---------------------------------------------------------------------------

@command("agent-status", aliases=["as"], description="Show status of all running agents",
         usage="/agent-status [agent_id]", category="agent", permission=PermLevel.READ_ONLY)
async def cmd_agent_status(ctx: CommandContext) -> CommandResult:
    target = ctx.args.strip()
    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available.", success=False)

    coordinator = _get_coordinator(brain)
    if coordinator:
        if target:
            status = coordinator.get_status(target)
            if status:
                elapsed = ""
                if status.get('created_at'):
                    elapsed = f"\n  Elapsed: {int(time.time() - status['created_at'])}s"
                return CommandResult(text=(
                    f"Agent: {status['id']}\n"
                    f"  Type:  {status['type']}\n"
                    f"  State: {status['state']}\n"
                    f"  Task:  {status['task']}"
                    f"{elapsed}"
                ))
            return CommandResult(text=f"Agent {target} not found.", success=False)

        running = coordinator.list_running()
        all_agents = coordinator._agents if hasattr(coordinator, '_agents') else {}
        total = len(all_agents)
        active = len(running)

        if total == 0:
            return CommandResult(text="No agents (running or completed).")

        lines = [f"Agent Status ({active} running, {total} total)", "=" * 50]
        for aid, handle in all_agents.items():
            status_icon = {
                "running": "\u27f3", "done": "\u2714", "failed": "\u2718",
                "pending": "\u25cb",
            }
            icon = status_icon.get(handle.status, "\u25cb")
            elapsed = int(time.time() - handle.created_at) if hasattr(handle, 'created_at') else 0
            lines.append(
                f"  {icon} [{handle.id}] {handle.agent_type:<10s} {handle.status:<10s} "
                f"{elapsed:>4d}s  {handle.task[:35]}"
            )
        return CommandResult(text="\n".join(lines))

    # Fallback: check _background_tasks
    bg = getattr(brain, '_background_tasks', {})
    if not bg:
        return CommandResult(text="No background agents running.")

    lines = [f"Background Tasks ({len(bg)})", "=" * 40]
    for tid, info in bg.items():
        state = "done" if info['task'].done() else "running"
        icon = "\u2714" if state == "done" else "\u27f3"
        lines.append(f"  {icon} [{tid}] {info['type']:<10s} {state:<10s} {info['desc'][:40]}")
    return CommandResult(text="\n".join(lines))
