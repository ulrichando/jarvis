"""JARVIS Enhanced Coordinator — advanced multi-agent orchestration.

Builds on the existing AgentCoordinator with:
- Task decomposition with dependency resolution
- Agent-to-task matching by capability
- Pipeline execution (sequential stages)
- Fan-out/fan-in parallel execution
- Progress tracking across multiple agents
- Result aggregation with conflict resolution
- Error recovery with retry and fallback

The base AgentCoordinator (coordinator.py) handles raw agent spawning,
worker tracking, and simple parallel/sequential/swarm modes. This module
adds the higher-level orchestration patterns.
"""

import asyncio
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from src.agent.coordinator import (
    AgentCoordinator,
    AgentHandle,
    CoordinatorMode,
    TaskNotification,
    WorkerState,
)

log = logging.getLogger("jarvis.coordinator_enhanced")


# ── Task Graph Types ─────────────────────────────────────────────────


class TaskStatus(Enum):
    PENDING = "pending"
    BLOCKED = "blocked"       # Waiting on dependencies
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRYING = "retrying"
    SKIPPED = "skipped"


class AgentCapability(Enum):
    """Agent capabilities used for task-agent matching."""
    READ_ONLY = "read_only"         # Research, analysis
    CODE_EDIT = "code_edit"         # Write/edit files
    BASH = "bash"                   # Run commands
    WEB = "web"                     # Web search/fetch
    FULL = "full"                   # All capabilities
    PLANNER = "planner"             # Task decomposition
    VERIFIER = "verifier"           # Test and verify


# Map agent types to their capabilities
AGENT_CAPABILITIES: dict[str, set[AgentCapability]] = {
    "scout": {AgentCapability.READ_ONLY, AgentCapability.WEB},
    "worker": {AgentCapability.FULL},
    "planner": {AgentCapability.READ_ONLY, AgentCapability.PLANNER},
    "verifier": {AgentCapability.READ_ONLY, AgentCapability.BASH, AgentCapability.VERIFIER},
}


@dataclass
class SubTask:
    """A unit of work in a task graph."""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    description: str = ""
    agent_type: str = "worker"
    status: TaskStatus = TaskStatus.PENDING
    dependencies: list[str] = field(default_factory=list)  # IDs of tasks this depends on
    result: str = ""
    error: str = ""
    agent_id: str = ""                  # Assigned agent/worker ID
    retries: int = 0
    max_retries: int = 2
    started_at: float = 0.0
    completed_at: float = 0.0
    priority: int = 0                    # Higher = more important
    required_capabilities: set[AgentCapability] = field(
        default_factory=lambda: {AgentCapability.FULL}
    )
    context: str = ""                    # Extra context passed to the agent

    @property
    def duration_ms(self) -> int:
        if self.started_at and self.completed_at:
            return int((self.completed_at - self.started_at) * 1000)
        return 0


@dataclass
class TaskGraph:
    """DAG of subtasks with dependency resolution."""
    goal: str = ""
    tasks: dict[str, SubTask] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def add_task(self, task: SubTask) -> None:
        self.tasks[task.id] = task

    def get_ready_tasks(self) -> list[SubTask]:
        """Return tasks whose dependencies are all completed."""
        ready = []
        for task in self.tasks.values():
            if task.status != TaskStatus.PENDING:
                continue
            deps_met = all(
                self.tasks[dep_id].status == TaskStatus.COMPLETED
                for dep_id in task.dependencies
                if dep_id in self.tasks
            )
            if deps_met:
                ready.append(task)
        # Sort by priority (higher first)
        ready.sort(key=lambda t: -t.priority)
        return ready

    def is_complete(self) -> bool:
        """True when all tasks are in a terminal state."""
        return all(
            t.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.SKIPPED)
            for t in self.tasks.values()
        )

    def has_failures(self) -> bool:
        return any(t.status == TaskStatus.FAILED for t in self.tasks.values())

    def get_results(self) -> dict[str, str]:
        """Collect results from completed tasks."""
        return {
            tid: t.result
            for tid, t in self.tasks.items()
            if t.status == TaskStatus.COMPLETED and t.result
        }

    def summary(self) -> str:
        """One-line status summary."""
        counts: dict[str, int] = {}
        for t in self.tasks.values():
            s = t.status.value
            counts[s] = counts.get(s, 0) + 1
        parts = [f"{c} {s}" for s, c in counts.items()]
        return f"{len(self.tasks)} tasks: {', '.join(parts)}"


# ── Progress Tracker ─────────────────────────────────────────────────


@dataclass
class ProgressUpdate:
    """Progress update emitted during orchestration."""
    task_id: str
    task_desc: str
    status: str
    message: str
    timestamp: float = field(default_factory=time.time)


class ProgressTracker:
    """Track progress across a task graph execution."""

    def __init__(self):
        self._updates: list[ProgressUpdate] = []
        self._callbacks: list[Callable[[ProgressUpdate], None]] = []

    def on_progress(self, callback: Callable[[ProgressUpdate], None]) -> None:
        """Register a progress callback."""
        self._callbacks.append(callback)

    def emit(self, task_id: str, task_desc: str, status: str, message: str) -> None:
        update = ProgressUpdate(
            task_id=task_id,
            task_desc=task_desc,
            status=status,
            message=message,
        )
        self._updates.append(update)
        for cb in self._callbacks:
            try:
                cb(update)
            except Exception as e:
                log.debug("Progress callback error: %s", e)

    def get_updates(self) -> list[ProgressUpdate]:
        return list(self._updates)

    def clear(self) -> None:
        self._updates.clear()


# ── Enhanced Coordinator ─────────────────────────────────────────────


class CoordinatorAgent:
    """Enhanced coordinator with task decomposition, dependency resolution,
    pipeline execution, and error recovery.

    Wraps the base AgentCoordinator for raw agent management and adds
    higher-level orchestration patterns for coordinator mode.
    """

    def __init__(self, base_coordinator: AgentCoordinator | None = None):
        self._base = base_coordinator or AgentCoordinator()
        self._active_graph: TaskGraph | None = None
        self._progress = ProgressTracker()
        self._reasoner = None  # Set via set_reasoner()

    def set_reasoner(self, reasoner: Any) -> None:
        """Set the LLM reasoner used for task decomposition and synthesis."""
        self._reasoner = reasoner

    @property
    def progress(self) -> ProgressTracker:
        return self._progress

    # ── Task Decomposition ───────────────────────────────────────────

    async def decompose(
        self,
        goal: str,
        max_tasks: int = 8,
        context: str = "",
    ) -> TaskGraph:
        """Break a complex goal into a task graph with dependencies.

        Uses the LLM to analyze the goal and produce a DAG of subtasks,
        each annotated with:
        - Required capabilities (read_only, code_edit, bash, etc.)
        - Dependencies (which tasks must complete first)
        - Priority level

        Args:
            goal: The high-level goal to decompose.
            max_tasks: Maximum number of subtasks.
            context: Additional context (codebase info, constraints).

        Returns:
            TaskGraph with subtasks and dependency edges.
        """
        if not self._reasoner:
            # Fallback: single task with no decomposition
            graph = TaskGraph(goal=goal)
            graph.add_task(SubTask(description=goal))
            return graph

        prompt = (
            f"Decompose this goal into {max_tasks} or fewer subtasks.\n\n"
            f"Goal: {goal}\n"
            f"{('Context: ' + context) if context else ''}\n\n"
            f"Return a JSON object with this structure:\n"
            f'{{"tasks": [\n'
            f'  {{"id": "t1", "description": "...", "agent_type": "scout|worker|planner|verifier", '
            f'"dependencies": [], "priority": 0}},\n'
            f"  ...\n"
            f"]}}\n\n"
            f"Rules:\n"
            f"- Each task should be self-contained and actionable\n"
            f"- Use 'scout' for read-only research, 'worker' for implementation\n"
            f"- Use 'verifier' for testing/verification tasks\n"
            f"- Dependencies are task IDs that must complete first\n"
            f"- Priority: higher number = more important (0-10)\n"
            f"- If the goal is simple, return a single task\n"
            f"- Return ONLY valid JSON, no explanation"
        )

        try:
            response = await self._reasoner.query(
                prompt,
                system_prompt="You decompose engineering tasks into parallel subtasks with dependency ordering. Return ONLY valid JSON.",
                history=None,
            )
            # Parse JSON from response
            response = response.strip()
            if "```" in response:
                lines = response.split("\n")
                response = "\n".join(l for l in lines if not l.strip().startswith("```"))
            start = response.find("{")
            end = response.rfind("}")
            if start != -1 and end != -1:
                response = response[start:end + 1]

            data = json.loads(response)
            graph = TaskGraph(goal=goal)

            for t in data.get("tasks", []):
                task = SubTask(
                    id=t.get("id", uuid.uuid4().hex[:8]),
                    description=t.get("description", ""),
                    agent_type=t.get("agent_type", "worker"),
                    dependencies=t.get("dependencies", []),
                    priority=t.get("priority", 0),
                    required_capabilities=AGENT_CAPABILITIES.get(
                        t.get("agent_type", "worker"),
                        {AgentCapability.FULL},
                    ),
                )
                graph.add_task(task)

            if not graph.tasks:
                # Fallback
                graph.add_task(SubTask(description=goal))

            log.info("Decomposed goal into %d tasks: %s", len(graph.tasks), goal[:60])
            return graph

        except Exception as e:
            log.warning("Task decomposition failed: %s, using single task", e)
            graph = TaskGraph(goal=goal)
            graph.add_task(SubTask(description=goal))
            return graph

    # ── Agent Assignment ─────────────────────────────────────────────

    def assign_agent(self, task: SubTask) -> str:
        """Match a subtask to the best agent type based on required capabilities.

        Returns the agent type string to use.
        """
        required = task.required_capabilities

        # Exact match first
        for agent_type, caps in AGENT_CAPABILITIES.items():
            if required == caps:
                return agent_type

        # Best overlap
        best_type = task.agent_type  # Use the decomposition's suggestion
        best_score = 0
        for agent_type, caps in AGENT_CAPABILITIES.items():
            overlap = len(required & caps)
            excess = len(caps - required)
            score = overlap * 2 - excess  # Prefer minimal excess capabilities
            if score > best_score:
                best_score = score
                best_type = agent_type

        return best_type

    # ── Execution Modes ──────────────────────────────────────────────

    async def execute_graph(
        self,
        graph: TaskGraph,
        timeout: float = 300,
        on_progress: Callable[[ProgressUpdate], None] | None = None,
    ) -> TaskGraph:
        """Execute a task graph, respecting dependencies.

        Tasks without unmet dependencies run in parallel. As tasks complete,
        newly unblocked tasks are launched. Continues until all tasks are in
        a terminal state or timeout is reached.

        Args:
            graph: The TaskGraph to execute.
            timeout: Maximum total execution time in seconds.
            on_progress: Optional callback for progress updates.

        Returns:
            The same TaskGraph with results filled in.
        """
        if not self._reasoner:
            log.error("Cannot execute graph: no reasoner set")
            return graph

        if on_progress:
            self._progress.on_progress(on_progress)

        self._active_graph = graph
        deadline = time.time() + timeout

        self._progress.emit("", graph.goal, "started",
                            f"Executing {len(graph.tasks)} tasks")

        while not graph.is_complete() and time.time() < deadline:
            # Find tasks ready to run
            ready = graph.get_ready_tasks()
            if not ready:
                # Nothing ready but graph not complete — blocked tasks waiting
                # for running tasks. Wait a bit.
                await asyncio.sleep(0.5)
                # Check running tasks for completion
                self._poll_running_tasks(graph)
                continue

            # Launch ready tasks in parallel
            for task in ready:
                agent_type = self.assign_agent(task)
                task.agent_type = agent_type
                task.status = TaskStatus.RUNNING
                task.started_at = time.time()

                # Build task context with dependency results
                task_context = self._build_task_context(task, graph)
                task.context = task_context

                # Spawn via base coordinator
                agent_id = self._base.spawn_worker(
                    agent_type=agent_type,
                    task=task.description,
                    context=task_context,
                )
                task.agent_id = agent_id
                self._base.update_worker(agent_id, "running")

                self._progress.emit(task.id, task.description, "running",
                                    f"Assigned to {agent_type} agent")
                log.info("Task %s -> %s agent %s: %s",
                         task.id, agent_type, agent_id, task.description[:60])

            # Wait for some tasks to complete
            await asyncio.sleep(1.0)
            self._poll_running_tasks(graph)

        # Handle timeout
        if not graph.is_complete():
            for task in graph.tasks.values():
                if task.status in (TaskStatus.RUNNING, TaskStatus.PENDING, TaskStatus.BLOCKED):
                    task.status = TaskStatus.FAILED
                    task.error = "Timeout"
                    if task.agent_id:
                        self._base.cancel_worker(task.agent_id)

        self._progress.emit("", graph.goal,
                            "completed" if not graph.has_failures() else "partial",
                            graph.summary())
        self._active_graph = None
        return graph

    def _poll_running_tasks(self, graph: TaskGraph) -> None:
        """Check running workers and update task statuses."""
        for task in graph.tasks.values():
            if task.status != TaskStatus.RUNNING:
                continue
            if not task.agent_id:
                continue

            ws = self._base.get_worker(task.agent_id)
            if not ws:
                continue

            if ws.status == "completed":
                task.status = TaskStatus.COMPLETED
                task.result = ws.result or ""
                task.completed_at = time.time()
                self._progress.emit(task.id, task.description, "completed",
                                    f"Done in {task.duration_ms}ms")

            elif ws.status == "failed":
                if task.retries < task.max_retries:
                    task.retries += 1
                    task.status = TaskStatus.PENDING  # Will be re-launched
                    task.agent_id = ""
                    self._progress.emit(task.id, task.description, "retrying",
                                        f"Retry {task.retries}/{task.max_retries}")
                    log.info("Task %s retrying (%d/%d)", task.id, task.retries, task.max_retries)
                else:
                    task.status = TaskStatus.FAILED
                    task.error = ws.result or "Unknown error"
                    task.completed_at = time.time()
                    self._progress.emit(task.id, task.description, "failed", task.error[:100])

            elif ws.status == "cancelled":
                task.status = TaskStatus.FAILED
                task.error = "Cancelled"
                task.completed_at = time.time()

    def _build_task_context(self, task: SubTask, graph: TaskGraph) -> str:
        """Build context for a task from its dependency results."""
        parts = [f"Goal: {graph.goal}"]
        parts.append(f"Your task: {task.description}")

        # Include results from completed dependencies
        dep_results = []
        for dep_id in task.dependencies:
            dep = graph.tasks.get(dep_id)
            if dep and dep.status == TaskStatus.COMPLETED and dep.result:
                dep_results.append(f"[From '{dep.description}']: {dep.result[:1500]}")

        if dep_results:
            parts.append("\nResults from prerequisite tasks:")
            parts.extend(dep_results)

        if task.context:
            parts.append(f"\nAdditional context: {task.context}")

        return "\n".join(parts)

    # ── Pipeline Execution ───────────────────────────────────────────

    async def execute_pipeline(
        self,
        goal: str,
        stages: list[dict[str, str]] | None = None,
        context: str = "",
        timeout: float = 300,
    ) -> str:
        """Execute a sequential pipeline of stages.

        Each stage's output becomes the next stage's input context.
        Default stages: research -> plan -> implement -> verify.

        Args:
            goal: The high-level goal.
            stages: List of {name, agent_type, prompt_template} dicts.
                    Template can use {goal}, {previous_result}, {context}.
            context: Additional context.
            timeout: Total timeout in seconds.

        Returns:
            Final stage result.
        """
        if not stages:
            stages = [
                {
                    "name": "research",
                    "agent_type": "scout",
                    "prompt": "Research: {goal}\n{context}\nReport findings — do not modify files.",
                },
                {
                    "name": "plan",
                    "agent_type": "planner",
                    "prompt": "Based on research findings:\n{previous_result}\n\nPlan implementation for: {goal}\nProvide specific file paths, changes, and order of operations.",
                },
                {
                    "name": "implement",
                    "agent_type": "worker",
                    "prompt": "Implement this plan:\n{previous_result}\n\nGoal: {goal}\nMake the changes, commit, and report what was done.",
                },
                {
                    "name": "verify",
                    "agent_type": "verifier",
                    "prompt": "Verify the implementation:\n{previous_result}\n\nGoal: {goal}\nRun tests, check for errors, prove the code works.",
                },
            ]

        previous_result = ""
        stage_timeout = timeout / len(stages)

        for i, stage in enumerate(stages):
            stage_name = stage.get("name", f"stage_{i}")
            agent_type = stage.get("agent_type", "worker")
            prompt_template = stage.get("prompt", "{goal}")

            # Format the prompt
            prompt = prompt_template.format(
                goal=goal,
                previous_result=previous_result,
                context=context,
            )

            self._progress.emit(stage_name, prompt[:80], "running",
                                f"Pipeline stage {i + 1}/{len(stages)}: {stage_name}")

            # Spawn and wait
            handle = self._base.spawn_agent(self._reasoner, agent_type, prompt, context)
            if handle._thread:
                handle._thread.join(timeout=stage_timeout)

            if handle.status == "done" and handle.result:
                previous_result = handle.result
                self._progress.emit(stage_name, prompt[:80], "completed",
                                    f"Stage {stage_name} done")
            else:
                error = handle.error or "Timeout"
                self._progress.emit(stage_name, prompt[:80], "failed",
                                    f"Stage {stage_name} failed: {error}")
                log.warning("Pipeline stage %s failed: %s", stage_name, error)
                # Continue with what we have
                if handle.result:
                    previous_result = handle.result

        return previous_result

    # ── Fan-out / Fan-in ─────────────────────────────────────────────

    async def fan_out(
        self,
        goal: str,
        tasks: list[str],
        agent_type: str = "worker",
        context: str = "",
        timeout: float = 180,
    ) -> list[dict[str, str]]:
        """Execute multiple independent tasks in parallel, collect all results.

        Args:
            goal: The overarching goal (shared context).
            tasks: List of task descriptions to run in parallel.
            agent_type: Agent type to use for all tasks.
            context: Shared context.
            timeout: Maximum wait time.

        Returns:
            List of {task, status, result} dicts.
        """
        handles = []
        for i, task_desc in enumerate(tasks):
            task_context = (
                f"You are worker {i + 1}/{len(tasks)}.\n"
                f"Main goal: {goal}\n"
                f"Your task: {task_desc}\n"
                f"{('Context: ' + context) if context else ''}\n"
                f"Focus on your task. Be thorough but concise."
            )
            h = self._base.spawn_agent(self._reasoner, agent_type, task_desc, task_context)
            handles.append((task_desc, h))

        # Wait for all
        for _, h in handles:
            if h._thread:
                h._thread.join(timeout=timeout)

        results = []
        for task_desc, h in handles:
            results.append({
                "task": task_desc,
                "status": h.status,
                "result": h.result[:3000] if h.result else "",
                "error": h.error,
            })

        return results

    async def fan_in(
        self,
        goal: str,
        results: list[dict[str, str]],
    ) -> str:
        """Aggregate results from fan-out into a coherent summary.

        Uses the LLM to synthesize multiple worker outputs.

        Args:
            goal: The original goal.
            results: List of {task, status, result} dicts from fan_out.

        Returns:
            Synthesized result string.
        """
        if not self._reasoner:
            # Fallback: just concatenate
            parts = [f"Results for: {goal}\n"]
            for i, r in enumerate(results):
                parts.append(f"\n--- Task {i + 1}: {r['task']} ({r['status']}) ---")
                if r.get("result"):
                    parts.append(r["result"])
            return "\n".join(parts)

        # Build synthesis prompt
        result_blocks = []
        for i, r in enumerate(results):
            status = r.get("status", "unknown")
            result_text = r.get("result", "No result")[:2000]
            result_blocks.append(
                f"Task {i + 1}: {r['task']}\n"
                f"Status: {status}\n"
                f"Result:\n{result_text}"
            )

        prompt = (
            f"Synthesize these worker results for the goal: {goal}\n\n"
            + "\n\n---\n\n".join(result_blocks) +
            "\n\nProvide a coherent summary of all findings. "
            "Note any conflicts or gaps. Be concise."
        )

        try:
            synthesis = await self._reasoner.query(
                prompt,
                system_prompt="You synthesize multiple research/implementation results into a coherent summary.",
                history=None,
            )
            return synthesis
        except Exception as e:
            log.warning("Fan-in synthesis failed: %s", e)
            # Fallback to concatenation
            parts = [f"Results for: {goal}\n"]
            for r in results:
                parts.append(f"\n--- {r['task']} ({r.get('status', '?')}) ---\n{r.get('result', '')}")
            return "\n".join(parts)

    # ── Coordinator Mode Control ─────────────────────────────────────

    def get_coordinator_prompt(self, mcp_servers: list[str] | None = None) -> str:
        """Get the coordinator system prompt (ported from coordinatorMode.ts).

        This is the prompt used when JARVIS operates in coordinator mode,
        directing workers rather than executing directly.

        Args:
            mcp_servers: List of connected MCP server names.

        Returns:
            System prompt string for coordinator mode.
        """
        worker_tools = "read_file, write_file, edit_file, bash, search_files, web_search, web_fetch"

        prompt = (
            "You are JARVIS operating in coordinator mode.\n\n"
            "## Your Role\n"
            "- Help the user achieve their goal\n"
            "- Direct workers to research, implement, and verify code changes\n"
            "- Synthesize results and communicate with the user\n"
            "- Answer questions directly when possible\n\n"
            "## Your Tools\n"
            "- **dispatch** — Spawn a new worker (scout, worker, planner, verifier)\n"
            "- Workers have access to: " + worker_tools + "\n"
        )

        if mcp_servers:
            prompt += f"\nWorkers also have MCP tools from: {', '.join(mcp_servers)}\n"

        prompt += (
            "\n## Task Workflow\n"
            "1. **Research** — Workers investigate (parallel)\n"
            "2. **Synthesis** — You read findings, craft implementation specs\n"
            "3. **Implementation** — Workers make changes per spec\n"
            "4. **Verification** — Workers test changes\n\n"
            "## Rules\n"
            "- Launch independent workers concurrently\n"
            "- Read-only tasks can run in parallel freely\n"
            "- Write tasks should be serialized per file set\n"
            "- Always synthesize research before delegating implementation\n"
            "- When a worker fails, retry with corrected instructions\n"
            "- Never fabricate results — wait for workers to report\n"
        )

        return prompt

    # ── Status ───────────────────────────────────────────────────────

    def get_status(self) -> dict:
        """Get current orchestration status."""
        base_status = self._base.get_status_summary()
        graph_status = self._active_graph.summary() if self._active_graph else "No active graph"
        return {
            "base": base_status,
            "graph": graph_status,
            "progress_updates": len(self._progress.get_updates()),
        }
