"""Hidden / debug commands -- internal tools and experimental features."""
import logging
import subprocess
import time
from pathlib import Path

from src.commands.registry import command, CommandContext, CommandResult, PermLevel

log = logging.getLogger("jarvis.commands.debug")


# ── /debug ─────────────────────────────────────────────────────────────

@command("debug", description="Toggle debug logging with optional filter",
         usage="/debug [on|off|filter <api|hooks|tools|mcp>]",
         category="core", permission=PermLevel.FULL, hidden=True)
async def cmd_debug(ctx: CommandContext) -> CommandResult:
    args = ctx.args.strip().lower()
    root = logging.getLogger("jarvis")
    brain = ctx.brain

    # Show current state if no args
    if not args:
        current = "ON" if root.level == logging.DEBUG else "OFF"
        active_filters = getattr(brain, '_debug_filters', set()) if brain else set()
        filter_str = ", ".join(sorted(active_filters)) if active_filters else "none"
        return CommandResult(
            text=f"Debug logging: {current}\n"
                 f"  Active filters: {filter_str}\n\n"
                 f"Usage:\n"
                 f"  /debug on          Enable all debug logging\n"
                 f"  /debug off         Disable debug logging\n"
                 f"  /debug api         Toggle API call logging\n"
                 f"  /debug hooks       Toggle hooks logging\n"
                 f"  /debug tools       Toggle tool execution logging\n"
                 f"  /debug mcp         Toggle MCP protocol logging"
        )

    # Valid filter names
    valid_filters = {"api", "hooks", "tools", "mcp"}

    if args == "on":
        root.setLevel(logging.DEBUG)
        if brain:
            brain._debug = True
        return CommandResult(text="Debug logging: ON (all categories)")

    elif args == "off":
        root.setLevel(logging.INFO)
        if brain:
            brain._debug = False
            brain._debug_filters = set()
        # Reset all sub-loggers
        for name in valid_filters:
            sub = logging.getLogger(f"jarvis.{name}")
            sub.setLevel(logging.INFO)
        return CommandResult(text="Debug logging: OFF")

    elif args in valid_filters:
        if not brain:
            return CommandResult(text="Brain not available.", success=False)

        if not hasattr(brain, '_debug_filters'):
            brain._debug_filters = set()

        # Map filter names to logger paths
        filter_loggers = {
            "api": ["jarvis.reasoning", "jarvis.providers"],
            "hooks": ["jarvis.hooks"],
            "tools": ["jarvis.agent.tools", "jarvis.agent.loop"],
            "mcp": ["jarvis.mcp"],
        }

        if args in brain._debug_filters:
            # Turn off this filter
            brain._debug_filters.discard(args)
            for logger_name in filter_loggers.get(args, []):
                logging.getLogger(logger_name).setLevel(logging.INFO)
            state = "OFF"
        else:
            # Turn on this filter
            brain._debug_filters.add(args)
            for logger_name in filter_loggers.get(args, []):
                logging.getLogger(logger_name).setLevel(logging.DEBUG)
            state = "ON"

        # If any filters active, ensure root is at least INFO (sub-loggers handle DEBUG)
        if brain._debug_filters:
            brain._debug = True
        else:
            brain._debug = False

        active = ", ".join(sorted(brain._debug_filters)) if brain._debug_filters else "none"
        return CommandResult(text=f"Debug filter '{args}': {state}\n  Active filters: {active}")

    else:
        return CommandResult(
            text=f"Unknown debug option: {args}\nUse: on, off, api, hooks, tools, mcp",
            success=False,
        )


# ── /benchmark ─────────────────────────────────────────────────────────

@command("benchmark", aliases=["bench"], description="Run performance benchmarks",
         usage="/benchmark [llm|tools|all]", category="core", permission=PermLevel.FULL, hidden=True)
async def cmd_benchmark(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    if not brain or not hasattr(brain, "reasoner"):
        return CommandResult(text="Reasoner not available for benchmarking.", success=False)

    target = ctx.args.strip().lower() or "all"
    lines = []

    # LLM response time benchmark
    if target in ("llm", "all"):
        prompt = "Respond with exactly: OK"
        iterations = 3
        times = []

        for i in range(iterations):
            t0 = time.perf_counter()
            try:
                await brain.reasoner.query(prompt, system_prompt="Respond briefly.")
            except Exception as e:
                lines.append(f"LLM benchmark failed on iteration {i + 1}: {e}")
                break
            elapsed = time.perf_counter() - t0
            times.append(elapsed)

        if times:
            avg = sum(times) / len(times)
            model = getattr(brain.reasoner, "active_model_name", "unknown")
            lines.append(f"LLM Benchmark ({model})")
            lines.append("=" * 40)
            for i, t in enumerate(times):
                lines.append(f"  Run {i + 1}: {t:.3f}s")
            lines.append(f"\n  Average: {avg:.3f}s")
            lines.append(f"  Min:     {min(times):.3f}s")
            lines.append(f"  Max:     {max(times):.3f}s")
            # Calculate tokens per second estimate
            lines.append(f"  TTFT:    ~{min(times):.3f}s (time to first token)")

    # Tool execution benchmark
    if target in ("tools", "all"):
        if lines:
            lines.append("")
        lines.append("Tool Execution Benchmark")
        lines.append("=" * 40)

        tool_tests = [
            ("read_file", "Read a small file"),
            ("bash", "Run echo command"),
            ("search_files", "Search for a pattern"),
        ]

        for tool_name, desc in tool_tests:
            t0 = time.perf_counter()
            try:
                if tool_name == "bash":
                    proc = subprocess.run(
                        ["echo", "benchmark"], capture_output=True, text=True, timeout=5,
                    )
                elif tool_name == "read_file":
                    _ = Path(__file__).read_text()[:100]
                elif tool_name == "search_files":
                    proc = subprocess.run(
                        ["grep", "-r", "--include=*.py", "-l", "CommandResult",
                         str(Path(__file__).parent)],
                        capture_output=True, text=True, timeout=5,
                    )
                elapsed = time.perf_counter() - t0
                lines.append(f"  {tool_name:<15s} {elapsed * 1000:.1f}ms  ({desc})")
            except Exception as e:
                elapsed = time.perf_counter() - t0
                lines.append(f"  {tool_name:<15s} FAIL ({e})")

    # Memory stats
    if target == "all":
        lines.append("")
        lines.append("System Stats")
        lines.append("=" * 40)
        if hasattr(brain, 'memory') and hasattr(brain.memory, 'count'):
            try:
                mem_count = brain.memory.count()
                lines.append(f"  Memory entries: {mem_count}")
            except Exception:
                lines.append("  Memory entries: unavailable")
        if hasattr(brain, 'tasks'):
            try:
                task_count = brain.tasks.count()
                lines.append(f"  Total tasks:    {task_count}")
            except Exception:
                pass
        if hasattr(brain, '_background_tasks'):
            lines.append(f"  Background:     {len(brain._background_tasks)} tasks")

    if not lines:
        return CommandResult(text="No benchmarks run. Use: /benchmark [llm|tools|all]", success=False)

    return CommandResult(text="\n".join(lines))


# ── /teleport ──────────────────────────────────────────────────────────

@command("teleport", aliases=["tp"], description="Teleport session to a different model or search workspace",
         usage="/teleport <model_name | file_or_symbol>",
         category="core", permission=PermLevel.FULL, hidden=True)
async def cmd_teleport(ctx: CommandContext) -> CommandResult:
    query = ctx.args.strip()
    if not query:
        return CommandResult(
            text="Usage:\n"
                 "  /teleport <model_name>      Switch to a different model/provider\n"
                 "  /teleport <file_or_symbol>   Search workspace for file or symbol\n\n"
                 "Examples:\n"
                 "  /teleport claude-opus        Switch to Claude Opus\n"
                 "  /teleport ollama:llama3      Switch to local Llama 3\n"
                 "  /teleport CommandRegistry    Find symbol in codebase",
            success=False,
        )

    brain = ctx.brain

    # Check if this looks like a model/provider specification
    known_providers = {"ollama", "groq", "openai", "anthropic", "xai", "together", "openrouter"}
    is_model_switch = False

    if ":" in query:
        provider_name = query.split(":")[0].lower()
        if provider_name in known_providers:
            is_model_switch = True
    elif brain and hasattr(brain, 'reasoner'):
        # Check if query matches a known model name pattern
        model_patterns = ["claude", "gpt", "llama", "mistral", "gemma", "qwen", "deepseek", "opus", "sonnet"]
        if any(p in query.lower() for p in model_patterns):
            is_model_switch = True

    # Model switch mode
    if is_model_switch:
        if not brain or not hasattr(brain, 'reasoner'):
            return CommandResult(text="Reasoner not available for model switch.", success=False)

        old_model = getattr(brain.reasoner, 'active_model_name', 'unknown')

        try:
            if ":" in query:
                provider_name, model_name = query.split(":", 1)
                if hasattr(brain.reasoner, 'set_provider'):
                    brain.reasoner.set_provider(provider_name, model_name)
                elif hasattr(brain.reasoner, 'switch_model'):
                    brain.reasoner.switch_model(provider_name, model_name)
                else:
                    return CommandResult(
                        text=f"Reasoner does not support model switching. "
                             f"Current model: {old_model}",
                        success=False,
                    )
            else:
                if hasattr(brain.reasoner, 'switch_model'):
                    brain.reasoner.switch_model(query)
                else:
                    return CommandResult(
                        text=f"Reasoner does not support model switching. "
                             f"Current model: {old_model}",
                        success=False,
                    )

            new_model = getattr(brain.reasoner, 'active_model_name', query)
            return CommandResult(
                text=f"Teleported session.\n"
                     f"  From: {old_model}\n"
                     f"  To:   {new_model}",
            )
        except Exception as e:
            return CommandResult(text=f"Model switch failed: {e}", success=False)

    # File/symbol search mode (fallback)
    results = []
    cwd = Path.cwd()

    # Search for matching filenames
    try:
        r = subprocess.run(
            ["find", str(cwd), "-maxdepth", "6", "-name", f"*{query}*",
             "-not", "-path", "*/.git/*", "-not", "-path", "*/__pycache__/*",
             "-not", "-path", "*/node_modules/*", "-not", "-path", "*/target/*"],
            capture_output=True, text=True, timeout=10,
        )
        if r.stdout.strip():
            for f in r.stdout.strip().splitlines()[:15]:
                results.append(f"  [file] {f}")
    except Exception:
        pass

    # Search for symbol definitions (def, class, function)
    try:
        r = subprocess.run(
            ["grep", "-rn", "--include=*.py", "--include=*.js", "--include=*.ts",
             "--include=*.rs", "--include=*.go",
             f"\\(def \\|class \\|fn \\|func \\|function \\){query}",
             str(cwd)],
            capture_output=True, text=True, timeout=10,
        )
        if r.stdout.strip():
            for line in r.stdout.strip().splitlines()[:15]:
                results.append(f"  [sym]  {line}")
    except Exception:
        pass

    if not results:
        return CommandResult(text=f"No results for '{query}'.")
    return CommandResult(
        text=f"Teleport: {query}\n{'=' * 40}\n" + "\n".join(results),
        data={"query": query, "count": len(results)},
    )


# ── /evolve ────────────────────────────────────────────────────────────

@command("evolve", description="Self-improvement: analyze recent errors and suggest improvements",
         usage="/evolve [days]", category="core", permission=PermLevel.FULL, hidden=True)
async def cmd_evolve(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available.", success=False)

    args = ctx.args.strip()
    days = 7
    if args:
        try:
            days = int(args)
        except ValueError:
            return CommandResult(text=f"Invalid days value: {args}. Use a number.", success=False)

    # Try the evolution engine on the brain
    if hasattr(brain, "evolution") and hasattr(brain.evolution, "evolve"):
        try:
            t0 = time.perf_counter()
            result = await brain.evolution.evolve(days=days)
            elapsed = time.perf_counter() - t0

            if isinstance(result, dict):
                lines = [
                    "Evolution Cycle Complete",
                    "=" * 40,
                    f"  Duration:      {elapsed:.1f}s",
                    f"  Analyzed:      last {days} days",
                ]
                if "opportunities" in result:
                    lines.append(f"  Opportunities: {result['opportunities']}")
                if "fixed" in result:
                    lines.append(f"  Fixed:         {result['fixed']}")
                if "score" in result:
                    lines.append(f"  Score:         {result['score']:.2f}")
                if "suggestions" in result:
                    lines.append(f"\n  Suggestions:")
                    for s in result["suggestions"][:10]:
                        lines.append(f"    - {s}")
                return CommandResult(text="\n".join(lines), data={"evolved": True})
            else:
                return CommandResult(
                    text=f"Evolution cycle complete ({elapsed:.1f}s).\n{result}",
                    data={"evolved": True},
                )
        except Exception as e:
            return CommandResult(text=f"Evolution failed: {e}", success=False)

    elif hasattr(brain, "evolution") and hasattr(brain.evolution, "run_cycle"):
        try:
            result = await brain.evolution.run_cycle()
            return CommandResult(
                text=f"Evolution cycle complete.\n{result}",
                data={"evolved": True},
            )
        except Exception as e:
            return CommandResult(text=f"Evolution failed: {e}", success=False)

    # Fallback: basic error analysis from recent conversation
    if hasattr(brain, 'memory') and hasattr(brain.memory, 'get_history'):
        try:
            history = brain.memory.get_history(limit=50)
            error_count = 0
            error_patterns = []
            for msg in history:
                content = msg.get("content", "") if isinstance(msg, dict) else str(msg)
                if any(w in content.lower() for w in ["error", "failed", "exception", "traceback"]):
                    error_count += 1
                    # Extract a brief pattern
                    for line in content.splitlines()[:3]:
                        if any(w in line.lower() for w in ["error", "failed", "exception"]):
                            error_patterns.append(line.strip()[:80])
                            break

            lines = [
                "Evolution Analysis (fallback mode)",
                "=" * 40,
                f"  Messages analyzed: {len(history)}",
                f"  Errors found:      {error_count}",
            ]
            if error_patterns:
                lines.append(f"\n  Error patterns:")
                for p in error_patterns[:5]:
                    lines.append(f"    - {p}")
                lines.append(f"\n  Suggestion: Review these patterns and create error handlers.")
            else:
                lines.append(f"\n  No significant errors detected. System is healthy.")
            return CommandResult(text="\n".join(lines))
        except Exception as e:
            return CommandResult(text=f"Analysis failed: {e}", success=False)

    return CommandResult(
        text="Evolution engine not wired. Connect brain.evolution to enable.\n"
             "Hint: The EvolutionEngine requires a Telemetry instance.",
        success=False,
    )


# ── /dream ─────────────────────────────────────────────────────────────

@command("dream-deep", description="Deep memory consolidation: extract and persist important memories",
         usage="/dream-deep", category="core", permission=PermLevel.FULL, hidden=True)
async def cmd_dream(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available.", success=False)

    lines = ["Entering dream state...", "=" * 40]
    consolidated_anything = False

    # Step 1: Auto-memory extraction from recent conversation
    try:
        from src.memory.auto_memory import MemoryExtractor

        history = []
        if hasattr(brain, 'memory') and hasattr(brain.memory, 'get_history'):
            try:
                history = brain.memory.get_history(limit=100)
            except Exception:
                pass
        elif hasattr(brain, 'conversation_history'):
            history = brain.conversation_history[-100:]

        if history:
            extractor = MemoryExtractor()
            memories = extractor.extract_from_messages(history)
            if memories:
                saved = extractor.save_memories(memories) if hasattr(extractor, 'save_memories') else len(memories)
                lines.append(f"  Auto-memory: {len(memories)} memories extracted, {saved} saved")
                for m in memories[:5]:
                    lines.append(f"    [{m.memory_type.value}] {m.description[:60]}")
                if len(memories) > 5:
                    lines.append(f"    ... and {len(memories) - 5} more")
                consolidated_anything = True
            else:
                lines.append("  Auto-memory: no new memories found in conversation")
        else:
            lines.append("  Auto-memory: no conversation history available")
    except ImportError:
        lines.append("  Auto-memory: module not available")
    except Exception as e:
        lines.append(f"  Auto-memory extraction failed: {e}")

    # Step 2: Memory consolidation (if memory service supports it)
    if hasattr(brain, "memory") and hasattr(brain.memory, "consolidate"):
        try:
            stats = await brain.memory.consolidate()
            lines.append(f"  Memory consolidated: {stats}")
            consolidated_anything = True
        except Exception as e:
            lines.append(f"  Memory consolidation failed: {e}")
    else:
        lines.append("  Memory consolidation: not available")

    # Step 3: Pattern extraction from recent interactions
    if hasattr(brain, "intelligence") and hasattr(brain.intelligence, "extract_patterns"):
        try:
            patterns = await brain.intelligence.extract_patterns()
            lines.append(f"  Patterns extracted: {len(patterns)}")
            for p in (patterns[:3] if isinstance(patterns, list) else []):
                lines.append(f"    - {p}")
            consolidated_anything = True
        except Exception as e:
            lines.append(f"  Pattern extraction failed: {e}")
    else:
        lines.append("  Pattern extraction: not available")

    # Step 4: Skill refinement
    if hasattr(brain, "skills") and hasattr(brain.skills, "refine"):
        try:
            refined = await brain.skills.refine()
            lines.append(f"  Skills refined: {refined}")
            consolidated_anything = True
        except Exception as e:
            lines.append(f"  Skill refinement failed: {e}")
    else:
        lines.append("  Skill refinement: not available")

    # Step 5: Neural lattice maintenance
    if hasattr(brain, "memory") and hasattr(brain.memory, "lattice"):
        try:
            lattice = brain.memory.lattice
            if hasattr(lattice, "prune"):
                pruned = lattice.prune()
                lines.append(f"  Neural lattice pruned: {pruned} weak connections removed")
                consolidated_anything = True
            elif hasattr(lattice, "stats"):
                stats = lattice.stats()
                lines.append(f"  Neural lattice: {stats}")
        except Exception as e:
            lines.append(f"  Neural lattice maintenance failed: {e}")

    if consolidated_anything:
        lines.append("\nDream cycle complete. Memories consolidated.")
    else:
        lines.append("\nDream cycle complete. No consolidation modules active.")

    return CommandResult(text="\n".join(lines))


# ── /self-modify ───────────────────────────────────────────────────────

@command("self-modify", aliases=["selfmod"], description="Self-modification: propose changes to JARVIS code",
         usage="/self-modify <target_description>", category="core",
         permission=PermLevel.DANGEROUS, hidden=True)
async def cmd_self_modify(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    desc = ctx.args.strip()
    if not desc:
        return CommandResult(
            text="Usage: /self-modify <description of desired behavior>\n\n"
                 "Examples:\n"
                 "  /self-modify add a greeting plugin that says good morning\n"
                 "  /self-modify create a tool that monitors CPU usage\n"
                 "  /self-modify improve error messages in the agent loop",
            success=False,
        )

    if not brain:
        return CommandResult(text="Brain not available.", success=False)

    # Determine modification strategy based on target
    target_lower = desc.lower()

    # Strategy 1: Plugin generation (safest)
    if any(w in target_lower for w in ["plugin", "add a", "create a", "new feature", "greeting"]):
        return await _self_modify_plugin(brain, desc)

    # Strategy 2: Code analysis and proposal (for existing code changes)
    if any(w in target_lower for w in ["improve", "fix", "refactor", "optimize", "change"]):
        return await _self_modify_proposal(brain, desc)

    # Default: plugin generation
    return await _self_modify_plugin(brain, desc)


async def _self_modify_plugin(brain, desc: str) -> CommandResult:
    """Generate a plugin from a description."""
    # Use agent loop if available, otherwise use reasoner directly
    prompt = (
        "Generate a JARVIS plugin (Python file) based on this description:\n"
        f"{desc}\n\n"
        "The plugin must follow this structure:\n"
        "```python\n"
        "PLUGIN_META = {\n"
        '    "name": "plugin_name",\n'
        '    "description": "what it does",\n'
        '    "version": "0.1.0",\n'
        "}\n\n"
        "def on_load(brain):\n"
        "    # Setup code\n"
        "    pass\n\n"
        "def on_message(brain, message):\n"
        "    # React to messages, return modified message or None\n"
        "    return None\n"
        "```\n\n"
        "Return ONLY the Python code, no explanation."
    )

    code = None
    try:
        if hasattr(brain, 'agent_loop'):
            code = await brain.agent_loop(prompt, max_steps=3)
        elif hasattr(brain, 'reasoner'):
            code = await brain.reasoner.query(prompt, system_prompt="You are a Python code generator.")
    except Exception as e:
        return CommandResult(text=f"Code generation failed: {e}", success=False)

    if not code:
        return CommandResult(text="No code generated.", success=False)

    # Extract code from markdown fences if present
    if "```python" in code:
        code = code.split("```python", 1)[1].split("```", 1)[0]
    elif "```" in code:
        code = code.split("```", 1)[1].split("```", 1)[0]
    code = code.strip()

    # Basic validation
    if "def " not in code:
        return CommandResult(
            text=f"Generated code does not contain function definitions. Output:\n{code[:500]}",
            success=False,
        )

    # Save to plugins directory
    plugin_dir = Path.home() / ".jarvis" / "plugins"
    plugin_dir.mkdir(parents=True, exist_ok=True)

    safe_name = "".join(c if c.isalnum() else "_" for c in desc[:30]).strip("_").lower()
    plugin_file = plugin_dir / f"{safe_name}.py"

    # Avoid overwriting
    if plugin_file.exists():
        plugin_file = plugin_dir / f"{safe_name}_{int(time.time()) % 10000}.py"

    plugin_file.write_text(code + "\n")

    # Reload plugins if available
    if hasattr(brain, 'plugins') and hasattr(brain.plugins, "reload"):
        try:
            brain.plugins.reload()
        except Exception:
            pass

    return CommandResult(
        text=f"Self-modification complete.\n"
             f"  Plugin saved: {plugin_file}\n"
             f"  Description:  {desc}\n"
             f"  Size:         {len(code)} bytes\n\n"
             f"Preview:\n{code[:300]}{'...' if len(code) > 300 else ''}",
        data={"plugin_path": str(plugin_file)},
    )


async def _self_modify_proposal(brain, desc: str) -> CommandResult:
    """Analyze codebase and propose improvements without writing code directly."""
    if not hasattr(brain, 'reasoner'):
        return CommandResult(text="Reasoner not available.", success=False)

    # Gather context about the target
    try:
        from src.agent.loop import _run_sub_agent

        analysis_prompt = (
            f"Analyze the JARVIS codebase and propose specific code changes for:\n{desc}\n\n"
            f"Find the relevant files, understand the current implementation, "
            f"and provide a detailed proposal with:\n"
            f"1. Files to modify\n"
            f"2. Specific changes (before/after)\n"
            f"3. Risks and testing needed\n"
            f"Do NOT make changes, only propose them."
        )

        proposal = await _run_sub_agent(brain.reasoner, "scout", analysis_prompt)
    except Exception as e:
        # Fallback to simple query
        try:
            proposal = await brain.reasoner.query(
                f"Propose improvements for: {desc}",
                system_prompt="You are analyzing a Python AI assistant codebase called JARVIS.",
            )
        except Exception as e2:
            return CommandResult(text=f"Analysis failed: {e2}", success=False)

    return CommandResult(
        text=f"Self-Modification Proposal\n"
             f"{'=' * 40}\n"
             f"Target: {desc}\n\n"
             f"{proposal}\n\n"
             f"To apply, use /worker with the specific changes.",
        data={"proposal": True},
    )
