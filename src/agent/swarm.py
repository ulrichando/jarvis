"""JARVIS Agent Swarm — Kimi K2.5-inspired parallel agent orchestration.

Inspired by:
- Kimi K2.5 PARL (Parallel-Agent Reinforcement Learning)
- OpenAI Swarm (handoff-based stateless agents)

Architecture:
1. Orchestrator receives a complex task
2. Decomposes into independent subtasks (via LLM)
3. Spawns N specialized sub-agents in parallel (async, not threads)
4. Each sub-agent executes its subtask using tools
5. Aggregator synthesizes all results into final output

Key differences from thread-based coordinator:
- All async (no threads, no nested event loops)
- Each agent is (name, instructions, tools) — lightweight
- Handoff via function return, not thread.join()
- Shared context_variables dict for cross-agent state
- Stateless — all state lives in messages
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field

log = logging.getLogger("jarvis.swarm")


@dataclass
class SwarmAgent:
    """A lightweight agent definition — just a role with tools."""
    name: str
    instructions: str  # System prompt for this agent
    tools: list[str] = field(default_factory=list)  # Tool names this agent can use
    max_iterations: int = 5


@dataclass
class SubtaskResult:
    """Result from a single subtask execution."""
    subtask: str
    agent_name: str
    status: str  # "done", "failed", "timeout"
    result: str
    duration_ms: int = 0
    tool_calls: int = 0


# ── Pre-defined specialist agents ──

SPECIALIST_AGENTS = {
    "coder": SwarmAgent(
        name="Coder",
        instructions="You are a code specialist. Write complete, working code. Use write_file for each file, bash for commands. No stubs or placeholders.",
        tools=["bash", "read_file", "write_file", "edit_file", "search_files"],
        max_iterations=8,
    ),
    "researcher": SwarmAgent(
        name="Researcher",
        instructions="You are a research specialist. Search the web, read sources, extract facts. Be thorough and cite sources.",
        tools=["web_search", "web_fetch", "read_file", "think"],
        max_iterations=6,
    ),
    "analyst": SwarmAgent(
        name="Analyst",
        instructions="You are an analysis specialist. Read code and data, identify patterns, issues, and improvements. Be specific with file paths and line numbers.",
        tools=["read_file", "search_files", "bash", "think"],
        max_iterations=6,
    ),
    "sysadmin": SwarmAgent(
        name="SysAdmin",
        instructions="You are a Linux system administrator. Run commands, check configs, install packages, manage services. You have sudo access (password: toor).",
        tools=["bash", "read_file", "write_file"],
        max_iterations=6,
    ),
    "writer": SwarmAgent(
        name="Writer",
        instructions="You synthesize information into clear, structured documents. Write reports, READMEs, documentation. Be thorough but concise.",
        tools=["write_file", "read_file", "think"],
        max_iterations=4,
    ),
}


class Swarm:
    """Async agent swarm — decompose, parallelize, aggregate."""

    def __init__(self, reasoner=None):
        self._reasoner = reasoner

    @property
    def reasoner(self):
        if self._reasoner is None:
            from src.reasoning.groq_client import GroqReasoner
            self._reasoner = GroqReasoner()
        return self._reasoner

    def set_reasoner(self, reasoner):
        self._reasoner = reasoner

    async def run(self, task: str, context: str = "",
                  max_agents: int = 5, timeout: float = 120,
                  on_progress=None) -> str:
        """Execute a complex task using parallel sub-agents.

        Flow:
        1. Decompose task into subtasks + assign specialist types
        2. Spawn async sub-agents for each subtask
        3. Wait for all to complete (with timeout)
        4. Aggregate results into final output

        Returns synthesized result string.
        """
        def progress(msg):
            if on_progress:
                on_progress(msg)
            log.info("Swarm: %s", msg)

        start = time.time()
        progress(f"Decomposing task: {task[:60]}...")

        # Step 1: Decompose
        plan = await self._decompose(task, context, max_agents)
        if not plan:
            progress("Cannot decompose — running as single agent")
            return await self._run_single(task, context, timeout)

        progress(f"Spawning {len(plan)} agents: {', '.join(p['agent'] for p in plan)}")

        # Step 2: Spawn all sub-agents as async tasks
        async_tasks = []
        for i, subtask_plan in enumerate(plan):
            async_tasks.append(
                self._run_subtask(
                    subtask=subtask_plan["subtask"],
                    agent_type=subtask_plan["agent"],
                    context=f"Main task: {task}\nYour role: subtask {i+1}/{len(plan)}\n{context}",
                    timeout=timeout,
                    on_progress=lambda msg, idx=i: progress(f"  Agent {idx+1}: {msg}"),
                )
            )

        # Step 3: Execute all in parallel
        progress("All agents working in parallel...")
        results = await asyncio.gather(*async_tasks, return_exceptions=True)

        # Step 4: Collect results
        subtask_results = []
        for i, (plan_item, result) in enumerate(zip(plan, results)):
            if isinstance(result, Exception):
                subtask_results.append(SubtaskResult(
                    subtask=plan_item["subtask"],
                    agent_name=plan_item["agent"],
                    status="failed",
                    result=str(result),
                ))
            else:
                subtask_results.append(result)

        succeeded = sum(1 for r in subtask_results if r.status == "done")
        elapsed = int((time.time() - start) * 1000)
        progress(f"Completed: {succeeded}/{len(subtask_results)} agents succeeded in {elapsed}ms")

        # Step 5: Aggregate
        report = await self._aggregate(task, subtask_results)
        return report

    async def _decompose(self, task: str, context: str, max_agents: int) -> list[dict] | None:
        """Decompose task into subtasks and assign specialist agents."""
        available = ", ".join(f"{k} ({v.name})" for k, v in SPECIALIST_AGENTS.items())

        prompt = (
            f"Decompose this task into {max_agents} or fewer independent subtasks "
            f"that can be done IN PARALLEL.\n\n"
            f"Task: {task}\n"
            f"{'Context: ' + context[:300] if context else ''}\n\n"
            f"Available specialist agents: {available}\n\n"
            f"Return ONLY a JSON array. Each item has 'subtask' (description) "
            f"and 'agent' (specialist type).\n"
            f"Example: [{{'subtask': 'Create the HTML file', 'agent': 'coder'}}, "
            f"{{'subtask': 'Research best practices', 'agent': 'researcher'}}]\n\n"
            f"If the task is too simple to parallelize, return []\n"
            f"IMPORTANT: Each subtask must be INDEPENDENT — no dependencies between them."
        )

        try:
            response = await self.reasoner.query(
                prompt,
                system_prompt="You decompose tasks for parallel execution. Return ONLY valid JSON array.",
                history=None,
            )
            response = response.strip()
            if "```" in response:
                lines = response.split("\n")
                response = "\n".join(l for l in lines if not l.strip().startswith("```"))
            start = response.find("[")
            end = response.rfind("]")
            if start != -1 and end != -1:
                plan = json.loads(response[start:end + 1])
                # Validate
                validated = []
                for item in plan[:max_agents]:
                    if isinstance(item, dict) and "subtask" in item:
                        agent = item.get("agent", "coder")
                        if agent not in SPECIALIST_AGENTS:
                            agent = "coder"
                        validated.append({"subtask": item["subtask"], "agent": agent})
                return validated if validated else None
        except Exception as e:
            log.debug("Task decomposition failed: %s", e)
        return None

    async def _run_subtask(self, subtask: str, agent_type: str, context: str,
                            timeout: float, on_progress=None) -> SubtaskResult:
        """Run a single subtask with a specialist agent."""
        agent = SPECIALIST_AGENTS.get(agent_type, SPECIALIST_AGENTS["coder"])
        start = time.time()

        if on_progress:
            on_progress(f"Starting: {subtask[:50]}")

        try:
            # Import here to avoid circular imports
            from src.agent.tools import TOOL_SCHEMAS, execute_tool

            # Filter tools for this specialist
            if agent.tools:
                tools = [t for t in TOOL_SCHEMAS if t.get("function", {}).get("name") in agent.tools]
            else:
                tools = TOOL_SCHEMAS

            # Build messages
            system = f"{agent.instructions}\n\nContext:\n{context}"
            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": subtask},
            ]

            # Mini agent loop for this subtask
            result_text = ""
            tool_call_count = 0

            for iteration in range(agent.max_iterations):
                try:
                    response = await asyncio.wait_for(
                        self.reasoner.query_with_tools(messages, tools),
                        timeout=timeout,
                    )
                except asyncio.TimeoutError:
                    return SubtaskResult(
                        subtask=subtask, agent_name=agent.name,
                        status="timeout", result=result_text or "Timed out",
                        duration_ms=int((time.time() - start) * 1000),
                        tool_calls=tool_call_count,
                    )

                text = response.get("text", "")
                tool_calls = response.get("tool_calls", [])

                if text:
                    result_text += text

                if not tool_calls:
                    break

                # Append assistant message
                msg = {"role": "assistant", "content": text or None}
                if tool_calls:
                    msg["tool_calls"] = [
                        {"id": tc["id"], "type": "function",
                         "function": {"name": tc["name"], "arguments": json.dumps(tc["args"])}}
                        for tc in tool_calls
                    ]
                messages.append(msg)

                # Execute tools
                for tc in tool_calls:
                    tool_call_count += 1
                    try:
                        tool_result = await asyncio.to_thread(
                            execute_tool, tc["name"], tc["args"]
                        )
                    except Exception as e:
                        tool_result = f"Error: {e}"

                    if len(tool_result) > 2000:
                        tool_result = tool_result[:2000] + "\n...(truncated)"

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": tool_result,
                    })

                    if on_progress:
                        on_progress(f"{tc['name']} ({iteration+1}/{agent.max_iterations})")

            elapsed = int((time.time() - start) * 1000)
            if on_progress:
                on_progress(f"Done ({elapsed}ms, {tool_call_count} tool calls)")

            return SubtaskResult(
                subtask=subtask, agent_name=agent.name,
                status="done", result=result_text[:3000],
                duration_ms=elapsed, tool_calls=tool_call_count,
            )

        except Exception as e:
            return SubtaskResult(
                subtask=subtask, agent_name=agent.name,
                status="failed", result=str(e),
                duration_ms=int((time.time() - start) * 1000),
            )

    async def _run_single(self, task: str, context: str, timeout: float) -> str:
        """Fallback: run as a single agent when decomposition fails."""
        return await self._run_subtask(
            subtask=task, agent_type="coder",
            context=context, timeout=timeout,
        )

    async def _aggregate(self, task: str, results: list[SubtaskResult]) -> str:
        """Aggregate results from all sub-agents into final output."""
        parts = []
        for r in results:
            icon = "+" if r.status == "done" else "x"
            parts.append(
                f"[{icon}] {r.agent_name}: {r.subtask}\n"
                f"   Status: {r.status} ({r.duration_ms}ms, {r.tool_calls} tool calls)\n"
                f"   Result: {r.result[:500]}"
            )
        results_text = "\n\n".join(parts)

        # If all agents succeeded, use LLM to synthesize
        all_done = all(r.status == "done" for r in results)
        if all_done and len(results) > 1:
            try:
                prompt = (
                    f"Task: {task}\n\n"
                    f"Results from {len(results)} parallel agents:\n\n{results_text}\n\n"
                    f"Synthesize these results into a clear, unified summary. "
                    f"Report what was accomplished and any important details."
                )
                synthesis = await self.reasoner.query(
                    prompt,
                    system_prompt="You synthesize results from multiple agents into a clear summary.",
                    history=None,
                )
                return synthesis
            except Exception:
                pass

        # Fallback: just format the raw results
        header = f"Swarm completed: {sum(1 for r in results if r.status == 'done')}/{len(results)} succeeded\n\n"
        return header + results_text
