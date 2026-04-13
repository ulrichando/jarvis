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


# ── /self-modify ───────────────────────────────────────────────────────

@command("self-modify", aliases=["selfmod"],
         description="Edit JARVIS source code to add or fix a capability",
         usage="/self-modify <description of what to add or fix>",
         category="core", permission=PermLevel.FULL, hidden=False)
async def cmd_self_modify(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    task = ctx.args.strip()

    if not task:
        return CommandResult(
            text=(
                "Usage: /self-modify <description>\n\n"
                "Examples:\n"
                "  /self-modify add a tool that reads system CPU temperature\n"
                "  /self-modify make the browser tool support drag-and-drop\n"
                "  /self-modify add a /ping command that checks if a host is reachable\n\n"
                "JARVIS will read his source, implement the change, deploy it, and confirm."
            ),
            success=False,
        )

    if not brain:
        return CommandResult(text="Brain not available.", success=False)

    prompt = (
        f"Self-modification task: {task}\n\n"
        f"Instructions:\n"
        f"1. Identify which source file needs the change:\n"
        f"   - New OS/system tools → src/agent/tools.py\n"
        f"   - Browser actions → src/agent/tools.py (browser tool)\n"
        f"   - New commands → src/commands/handlers/\n"
        f"   - Server endpoints → src/server/web_server.py\n"
        f"   - Core behavior → src/brain.py\n"
        f"2. Use read_file to read the relevant file\n"
        f"3. Use edit_file or write_file to implement the change\n"
        f"4. Run: bash /home/ulrich/Documents/Projects/jarvis/scripts/self-deploy.sh --python\n"
        f"5. Confirm the change was applied successfully"
    )

    result_text = ""
    try:
        async for event in brain.think_stream(prompt):
            if event.get("type") == "text":
                result_text += event["content"]
    except Exception as e:
        return CommandResult(text=f"Self-modification failed: {e}", success=False)

    return CommandResult(text=result_text or "Self-modification complete.")


# ── /logs ─────────────────────────────────────────────────────────────────

@command("logs", description="Show recent JARVIS conversation logs and context",
         usage="/logs [n] [--context | --memory | --screen | --tail]",
         category="core", permission=PermLevel.READ_ONLY)
async def cmd_logs(ctx: CommandContext) -> CommandResult:
    """Show recent conversation history and injected context for debugging."""
    import sqlite3, os, time
    args = ctx.args.strip().lower()

    show_context = "--context" in args
    show_memory  = "--memory" in args
    show_screen  = "--screen" in args
    show_tail    = "--tail" in args

    # Parse line count
    n = 10
    for part in args.split():
        if part.isdigit():
            n = min(int(part), 50)

    lines = []

    # ── Conversation history ──────────────────────────────────────────────
    db_candidates = [
        os.path.expanduser("~/.jarvis/data/jarvis.db"),
        os.path.expanduser("~/.jarvis/data/memory.sqlite"),
        os.path.expanduser("~/.jarvis/memory.db"),
    ]
    db_path = next((p for p in db_candidates if os.path.exists(p)), None)

    if db_path:
        try:
            conn = sqlite3.connect(db_path, timeout=5)
            rows = conn.execute(
                "SELECT role, content, timestamp FROM conversations "
                "ORDER BY rowid DESC LIMIT ?", (n,)
            ).fetchall()
            conn.close()
            if rows:
                lines.append(f"── Last {len(rows)} messages ({'from ' + db_path}) ──")
                for role, content, ts in reversed(rows):
                    label = "You" if role == "user" else "JARVIS"
                    # Truncate long messages
                    preview = content[:200].replace("\n", " ")
                    if len(content) > 200:
                        preview += f"... [{len(content)} chars]"
                    lines.append(f"  [{label}] {preview}")
        except Exception as e:
            lines.append(f"  DB error: {e}")
    else:
        lines.append("  No conversation DB found.")

    # ── Screen context ─────────────────────────────────────────────────
    if show_screen:
        lines.append("\n── Screen Context ──")
        brain = ctx.brain
        if brain and hasattr(brain, "screen"):
            sc = brain.screen.get_context_for_llm()
            lines.append(sc if sc else "  (no screen context)")
        else:
            lines.append("  (screen observer not available)")

    # ── Memory context ─────────────────────────────────────────────────
    if show_memory:
        lines.append("\n── Memory Recall (for 'hello') ──")
        brain = ctx.brain
        if brain and hasattr(brain, "memory"):
            try:
                mc = brain.memory.recall_as_context("hello", top_k=3)
                lines.append(mc if mc else "  (no memory context)")
            except Exception as e:
                lines.append(f"  Memory error: {e}")
        else:
            lines.append("  (memory not available)")

    # ── Web server log tail ────────────────────────────────────────────
    if show_tail:
        log_path = "/tmp/jarvis-web.log"
        if os.path.exists(log_path):
            lines.append("\n── Web Server Log (last 30 lines) ──")
            with open(log_path) as f:
                tail = f.readlines()[-30:]
            lines.extend(l.rstrip() for l in tail)
        else:
            lines.append("\n  No web server log at /tmp/jarvis-web.log")

    lines.append("\nUsage: /logs [n] [--context] [--memory] [--screen] [--tail]")
    return CommandResult(text="\n".join(lines))
