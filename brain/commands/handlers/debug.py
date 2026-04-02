"""Hidden / debug commands — internal tools and experimental features."""
import logging
import subprocess
import time
from pathlib import Path

from brain.commands.registry import command, CommandContext, CommandResult, PermLevel


# ── /debug ─────────────────────────────────────────────────────────────

@command("debug", description="Toggle debug logging",
         usage="/debug", category="core", permission=PermLevel.FULL, hidden=True)
async def cmd_debug(ctx: CommandContext) -> CommandResult:
    root = logging.getLogger("jarvis")
    if root.level == logging.DEBUG:
        root.setLevel(logging.INFO)
        state = "OFF"
    else:
        root.setLevel(logging.DEBUG)
        state = "ON"

    # Also toggle brain debug flag if available
    brain = ctx.brain
    if brain:
        brain._debug = (state == "ON")

    return CommandResult(text=f"Debug logging: {state}")


# ── /benchmark ─────────────────────────────────────────────────────────

@command("benchmark", aliases=["bench"], description="Benchmark LLM response times",
         usage="/benchmark", category="core", permission=PermLevel.FULL, hidden=True)
async def cmd_benchmark(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    if not brain or not hasattr(brain, "reasoner"):
        return CommandResult(text="Reasoner not available for benchmarking.", success=False)

    prompt = "Respond with exactly: OK"
    iterations = 3
    times = []

    for i in range(iterations):
        t0 = time.perf_counter()
        try:
            await brain.reasoner.generate(prompt, max_tokens=10)
        except Exception as e:
            return CommandResult(text=f"Benchmark failed on iteration {i+1}: {e}", success=False)
        elapsed = time.perf_counter() - t0
        times.append(elapsed)

    avg = sum(times) / len(times)
    model = getattr(brain.reasoner, "active_model_name", "unknown")
    lines = [
        f"LLM Benchmark ({model})",
        "=" * 40,
    ]
    for i, t in enumerate(times):
        lines.append(f"  Run {i+1}: {t:.3f}s")
    lines.append(f"\n  Average: {avg:.3f}s")
    lines.append(f"  Min:     {min(times):.3f}s")
    lines.append(f"  Max:     {max(times):.3f}s")
    return CommandResult(text="\n".join(lines))


# ── /teleport ──────────────────────────────────────────────────────────

@command("teleport", aliases=["tp"], description="Search workspace for file or symbol",
         usage="/teleport <symbol>", category="core", permission=PermLevel.READ_ONLY, hidden=True)
async def cmd_teleport(ctx: CommandContext) -> CommandResult:
    query = ctx.args.strip()
    if not query:
        return CommandResult(text="Usage: /teleport <file_or_symbol>", success=False)

    results = []
    cwd = Path.cwd()

    # Search for matching filenames
    try:
        r = subprocess.run(
            ["find", str(cwd), "-maxdepth", "6", "-name", f"*{query}*",
             "-not", "-path", "*/.git/*", "-not", "-path", "*/__pycache__/*",
             "-not", "-path", "*/node_modules/*"],
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
             f"(def |class |fn |func |function ){query}",
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
        text=f"Teleport: {query}\n{'─' * 40}\n" + "\n".join(results),
        data={"query": query, "count": len(results)},
    )


# ── /evolve ────────────────────────────────────────────────────────────

@command("evolve", description="Trigger the evolution engine",
         usage="/evolve", category="core", permission=PermLevel.FULL, hidden=True)
async def cmd_evolve(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available.", success=False)

    if hasattr(brain, "evolution") and hasattr(brain.evolution, "run_cycle"):
        try:
            result = await brain.evolution.run_cycle()
            return CommandResult(
                text=f"Evolution cycle complete.\n{result}",
                data={"evolved": True},
            )
        except Exception as e:
            return CommandResult(text=f"Evolution failed: {e}", success=False)

    return CommandResult(
        text="Evolution engine not wired. Connect brain.evolution to enable.",
        success=False,
    )


# ── /dream ─────────────────────────────────────────────────────────────

@command("dream", description="Trigger offline learning / memory consolidation",
         usage="/dream", category="core", permission=PermLevel.FULL, hidden=True)
async def cmd_dream(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available.", success=False)

    lines = ["Entering dream state...", "─" * 40]

    # Memory consolidation
    if hasattr(brain, "memory") and hasattr(brain.memory, "consolidate"):
        try:
            stats = await brain.memory.consolidate()
            lines.append(f"  Memory consolidated: {stats}")
        except Exception as e:
            lines.append(f"  Memory consolidation failed: {e}")
    else:
        lines.append("  Memory consolidation: not available")

    # Pattern extraction from recent interactions
    if hasattr(brain, "intelligence") and hasattr(brain.intelligence, "extract_patterns"):
        try:
            patterns = await brain.intelligence.extract_patterns()
            lines.append(f"  Patterns extracted: {len(patterns)}")
        except Exception as e:
            lines.append(f"  Pattern extraction failed: {e}")
    else:
        lines.append("  Pattern extraction: not available")

    # Skill refinement
    if hasattr(brain, "skills") and hasattr(brain.skills, "refine"):
        try:
            refined = await brain.skills.refine()
            lines.append(f"  Skills refined: {refined}")
        except Exception as e:
            lines.append(f"  Skill refinement failed: {e}")
    else:
        lines.append("  Skill refinement: not available")

    lines.append("\nDream cycle complete.")
    return CommandResult(text="\n".join(lines))


# ── /self-modify ───────────────────────────────────────────────────────

@command("self-modify", aliases=["selfmod"], description="Create a plugin from description",
         usage="/self-modify <description>", category="core",
         permission=PermLevel.DANGEROUS, hidden=True)
async def cmd_self_modify(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    desc = ctx.args.strip()
    if not desc:
        return CommandResult(text="Usage: /self-modify <description of desired behavior>", success=False)

    if not brain or not hasattr(brain, "agent_loop"):
        return CommandResult(text="Agent not available for self-modification.", success=False)

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
        "    # React to messages\n"
        "    return None  # or modified message\n"
        "```\n\n"
        "Return ONLY the Python code, no explanation."
    )

    try:
        code = await brain.agent_loop(prompt, max_steps=3)

        # Extract code from markdown fences if present
        if "```python" in code:
            code = code.split("```python", 1)[1].split("```", 1)[0]
        elif "```" in code:
            code = code.split("```", 1)[1].split("```", 1)[0]
        code = code.strip()

        # Save to plugins directory
        plugin_dir = Path.home() / ".jarvis" / "plugins"
        plugin_dir.mkdir(parents=True, exist_ok=True)

        # Derive name from description
        safe_name = "".join(c if c.isalnum() else "_" for c in desc[:30]).strip("_").lower()
        plugin_file = plugin_dir / f"{safe_name}.py"
        plugin_file.write_text(code + "\n")

        # Reload plugins if available
        if hasattr(brain.plugins, "reload"):
            brain.plugins.reload()

        return CommandResult(
            text=f"Self-modification complete.\n"
                 f"  Plugin saved: {plugin_file}\n"
                 f"  Description: {desc}",
            data={"plugin_path": str(plugin_file)},
        )
    except Exception as e:
        return CommandResult(text=f"Self-modification failed: {e}", success=False)
