"""JARVIS Agent Coordinator — thread-based multi-agent swarm.

Supports:
- parallel: Multiple agents work on DIFFERENT subtasks simultaneously
- sequential: Each agent builds on previous output
- pipeline: Scout → Planner → Worker chain
- swarm: Planner decomposes task → N workers execute in parallel → Merger combines results
- synthesize: Research → synthesize findings → delegate implementation (Claude Code pattern)

Agents run on OS threads for isolation. Each gets its own event loop.
"""

import threading
import asyncio
import uuid
import json
import re
import time
import logging
from dataclasses import dataclass, field
from enum import Enum

log = logging.getLogger("jarvis.coordinator")


# ── Coordinator Modes ──


class CoordinatorMode(Enum):
    """How the coordinator orchestrates agents."""
    DIRECT = "direct"          # Current behavior: spawn and wait
    SYNTHESIZE = "synthesize"  # Research → synthesize findings → delegate implementation
    PARALLEL = "parallel"      # Launch multiple workers simultaneously, collect results


# ── Notification & Worker State ──


@dataclass
class TaskNotification:
    """Notification emitted when a worker task changes state."""
    task_id: str
    agent_type: str
    status: str          # pending, running, completed, failed, cancelled
    summary: str
    result: str
    token_usage: int = 0
    duration_ms: int = 0


@dataclass
class WorkerState:
    """Lightweight tracking state for a spawned worker."""
    agent_id: str
    agent_type: str
    task: str
    status: str           # pending, running, completed, failed, cancelled
    is_backgrounded: bool = False
    result: str | None = None
    started_at: float = field(default_factory=time.time)
    completed_at: float | None = None


@dataclass
class AgentHandle:
    id: str
    agent_type: str
    task: str
    status: str = "pending"  # pending, running, done, failed, cancelled
    created_at: float = field(default_factory=time.time)
    result: str = ""
    error: str = ""
    _thread: threading.Thread | None = field(default=None, repr=False)


class AgentCoordinator:
    """Thread-based multi-agent coordinator with swarm decomposition."""

    def __init__(self, mode: CoordinatorMode = CoordinatorMode.DIRECT):
        self._agents: dict[str, AgentHandle] = {}
        self._lock = threading.Lock()
        # New coordinator-pattern state
        self._mode: CoordinatorMode = mode
        self._workers: dict[str, WorkerState] = {}
        self._notifications: list[TaskNotification] = []

    def spawn_agent(self, reasoner, agent_type: str, task: str, context: str = "") -> AgentHandle:
        """Spawn an agent on a new thread. Returns handle immediately."""
        handle = AgentHandle(
            id=uuid.uuid4().hex[:8],
            agent_type=agent_type,
            task=task,
            status="running",
        )

        def _run_agent():
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                from brain.agent.loop import _run_sub_agent
                result = loop.run_until_complete(
                    _run_sub_agent(reasoner, agent_type, task, context)
                )
                handle.result = result
                handle.status = "done"
                log.info("Agent %s (%s) completed", handle.id, agent_type)
            except Exception as e:
                handle.error = str(e)
                handle.status = "failed"
                log.error("Agent %s failed: %s", handle.id, e)
            finally:
                try:
                    loop.close()
                except Exception:
                    pass

        thread = threading.Thread(
            target=_run_agent,
            name=f"jarvis-agent-{handle.id}",
            daemon=True,
        )
        handle._thread = thread

        with self._lock:
            self._agents[handle.id] = handle

        thread.start()
        return handle

    # ── Worker Tracking (Claude Code coordinator pattern) ──

    def spawn_worker(self, agent_type: str, task: str, context: str = "",
                     background: bool = False) -> str:
        """Register a new worker and return its agent_id (UUID hex)."""
        agent_id = uuid.uuid4().hex[:12]
        ws = WorkerState(
            agent_id=agent_id,
            agent_type=agent_type,
            task=task,
            status="pending",
            is_backgrounded=background,
            started_at=time.time(),
        )
        with self._lock:
            self._workers[agent_id] = ws
        self._emit_notification(ws, summary=f"Spawned {agent_type} worker")
        log.info("Worker %s spawned: type=%s bg=%s task=%s",
                 agent_id, agent_type, background, task[:80])
        return agent_id

    def update_worker(self, agent_id: str, status: str, result: str = "") -> None:
        """Update worker state and emit notification on terminal states."""
        with self._lock:
            ws = self._workers.get(agent_id)
        if not ws:
            log.warning("update_worker: unknown agent_id %s", agent_id)
            return
        ws.status = status
        if result:
            ws.result = result
        if status in ("completed", "failed", "cancelled"):
            ws.completed_at = time.time()
            duration_ms = int((ws.completed_at - ws.started_at) * 1000)
            self._emit_notification(
                ws,
                summary=f"Worker {ws.agent_type} {status}",
                duration_ms=duration_ms,
            )
        log.debug("Worker %s -> %s", agent_id, status)

    def get_worker(self, agent_id: str) -> WorkerState | None:
        with self._lock:
            return self._workers.get(agent_id)

    def get_active_workers(self) -> list[WorkerState]:
        """Return workers that are still pending or running."""
        with self._lock:
            return [w for w in self._workers.values()
                    if w.status in ("pending", "running")]

    def get_completed_workers(self) -> list[WorkerState]:
        """Return workers that have finished (completed, failed, cancelled)."""
        with self._lock:
            return [w for w in self._workers.values()
                    if w.status in ("completed", "failed", "cancelled")]

    def cancel_worker(self, agent_id: str) -> bool:
        """Mark a worker as cancelled. Returns False if not found."""
        with self._lock:
            ws = self._workers.get(agent_id)
        if not ws:
            return False
        if ws.status in ("completed", "failed", "cancelled"):
            return False  # already terminal
        self.update_worker(agent_id, "cancelled")
        return True

    def should_continue_or_spawn(self, agent_id: str, new_task: str) -> str:
        """Decide whether an existing worker should continue or a fresh one should spawn.

        Heuristic: extract file-path-like tokens from both the worker's original
        task and the new task. If >50% of paths in new_task overlap with the
        worker's task context, return 'continue'; otherwise 'spawn'.
        """
        ws = self.get_worker(agent_id)
        if not ws:
            return "spawn"
        if ws.status not in ("running", "completed"):
            return "spawn"

        def _extract_paths(text: str) -> set[str]:
            # Match unix-style paths and dotted module names
            return set(re.findall(r'[\w./\-]+(?:\.[\w]+)+|/[\w./\-]+', text))

        old_paths = _extract_paths(ws.task)
        new_paths = _extract_paths(new_task)
        if not new_paths:
            return "spawn"
        overlap = len(old_paths & new_paths) / len(new_paths)
        return "continue" if overlap > 0.5 else "spawn"

    def format_notification(self, worker: WorkerState) -> str:
        """Format worker state as an XML task-notification block."""
        summary = f"Worker {worker.agent_type}: {worker.task[:120]}"
        result_text = (worker.result or "")[:2000]
        duration = ""
        if worker.completed_at and worker.started_at:
            duration = f"\n  <duration-ms>{int((worker.completed_at - worker.started_at) * 1000)}</duration-ms>"
        return (
            f"<task-notification>\n"
            f"  <task-id>{worker.agent_id}</task-id>\n"
            f"  <status>{worker.status}</status>\n"
            f"  <summary>{summary}</summary>\n"
            f"  <result>{result_text}</result>"
            f"{duration}\n"
            f"</task-notification>"
        )

    def get_status_summary(self) -> str:
        """Human-readable one-liner: '3 workers: 2 running, 1 completed'."""
        with self._lock:
            workers = list(self._workers.values())
        if not workers:
            return "0 workers"
        counts: dict[str, int] = {}
        for w in workers:
            counts[w.status] = counts.get(w.status, 0) + 1
        parts = [f"{c} {s}" for s, c in counts.items()]
        return f"{len(workers)} workers: {', '.join(parts)}"

    def drain_notifications(self) -> list[TaskNotification]:
        """Return and clear pending notifications."""
        with self._lock:
            notifs = list(self._notifications)
            self._notifications.clear()
        return notifs

    def _emit_notification(self, ws: WorkerState, summary: str = "",
                           token_usage: int = 0, duration_ms: int = 0) -> None:
        notif = TaskNotification(
            task_id=ws.agent_id,
            agent_type=ws.agent_type,
            status=ws.status,
            summary=summary or ws.task[:120],
            result=ws.result or "",
            token_usage=token_usage,
            duration_ms=duration_ms,
        )
        with self._lock:
            self._notifications.append(notif)

    # ── Swarm Mode: decompose + parallel workers + merge ──

    async def swarm(self, reasoner, task: str, context: str = "",
                    max_workers: int = 5, timeout: float = 180) -> str:
        """Swarm execution: decompose task into subtasks, run workers in parallel, merge results.

        Flow:
        1. Planner agent decomposes task into N independent subtasks
        2. N worker agents execute subtasks in parallel threads
        3. Results are merged into final output

        Returns the merged result string.
        """
        log.info("Swarm: decomposing task: %s", task[:60])

        # Step 1: Decompose task into subtasks using the LLM
        subtasks = await self._decompose_task(reasoner, task, max_workers)
        if not subtasks:
            # Can't decompose — run as single agent
            log.info("Swarm: no decomposition possible, running single agent")
            handle = self.spawn_agent(reasoner, "worker", task, context)
            handle._thread.join(timeout=timeout)
            return handle.result

        log.info("Swarm: decomposed into %d subtasks", len(subtasks))

        # Step 2: Spawn worker agents for each subtask
        handles = []
        for i, subtask in enumerate(subtasks):
            sub_context = f"You are worker {i+1}/{len(subtasks)} in a swarm.\n"
            sub_context += f"Main task: {task}\n"
            sub_context += f"Your specific subtask: {subtask}\n"
            if context:
                sub_context += f"Additional context: {context}\n"
            sub_context += "Focus ONLY on your subtask. Be thorough but concise."

            h = self.spawn_agent(reasoner, "worker", subtask, sub_context)
            handles.append((subtask, h))
            log.info("Swarm worker %d: %s", i + 1, subtask[:60])

        # Step 3: Wait for all workers
        for _, h in handles:
            if h._thread:
                h._thread.join(timeout=timeout)

        # Step 4: Merge results
        results = []
        for subtask, h in handles:
            status = "done" if h.status == "done" else f"failed: {h.error}"
            results.append({
                "subtask": subtask,
                "status": status,
                "result": h.result[:2000] if h.result else "",
            })

        # Build merged output
        parts = [f"Swarm completed {len(results)} subtasks for: {task}\n"]
        for i, r in enumerate(results):
            parts.append(f"\n--- Subtask {i+1}: {r['subtask']} ---")
            parts.append(f"Status: {r['status']}")
            if r['result']:
                parts.append(r['result'])

        merged = "\n".join(parts)
        log.info("Swarm completed: %d/%d successful",
                 sum(1 for r in results if r['status'] == 'done'), len(results))
        return merged

    async def _decompose_task(self, reasoner, task: str, max_subtasks: int) -> list[str]:
        """Use LLM to break a task into independent subtasks."""
        prompt = (
            f"Break this task into {max_subtasks} or fewer independent subtasks that can be done in parallel.\n\n"
            f"Task: {task}\n\n"
            f"Return ONLY a JSON array of strings, each being a specific subtask.\n"
            f"Each subtask should be self-contained and actionable.\n"
            f"If the task is simple and can't be split, return an empty array [].\n\n"
            f"Example: [\"Create manifest.json with extension config\", \"Create popup.html with chat UI\", \"Create background.js with WebSocket connection\"]"
        )
        try:
            response = await reasoner.query(
                prompt,
                system_prompt="You decompose tasks into parallel subtasks. Return ONLY a valid JSON array of strings. No explanation.",
                history=None,
            )
            response = response.strip()
            # Extract JSON array from response
            if "```" in response:
                lines = response.split("\n")
                response = "\n".join(l for l in lines if not l.strip().startswith("```"))
            # Find the array in the response
            start = response.find("[")
            end = response.rfind("]")
            if start != -1 and end != -1:
                response = response[start:end+1]
            subtasks = json.loads(response)
            if isinstance(subtasks, list) and all(isinstance(s, str) for s in subtasks):
                return subtasks[:max_subtasks]
        except Exception as e:
            log.debug("Task decomposition failed: %s", e)
        return []

    # ── Team Mode (original) ──

    def spawn_team(self, reasoner, task: str, agent_types: list[str],
                   strategy: str = "parallel", context: str = "") -> list[AgentHandle]:
        """Spawn multiple agents as a team."""
        if strategy == "parallel":
            handles = []
            for at in agent_types:
                h = self.spawn_agent(reasoner, at, task, context)
                handles.append(h)
            return handles

        elif strategy == "sequential":
            handles = []
            accumulated = context
            for at in agent_types:
                h = self.spawn_agent(reasoner, at, task, accumulated)
                h._thread.join(timeout=120)
                accumulated = f"{accumulated}\n\nPrevious ({at}):\n{h.result[:2000]}"
                handles.append(h)
            return handles

        elif strategy == "pipeline":
            pipeline = agent_types or ["scout", "planner", "worker"]
            return self.spawn_team(reasoner, task, pipeline, strategy="sequential", context=context)

        return []

    # ── Status & Management ──

    def wait_for(self, agent_id: str, timeout: float = 120) -> AgentHandle | None:
        with self._lock:
            handle = self._agents.get(agent_id)
        if not handle or not handle._thread:
            return handle
        handle._thread.join(timeout=timeout)
        return handle

    def wait_all(self, handles: list[AgentHandle], timeout: float = 120) -> list[AgentHandle]:
        for h in handles:
            if h._thread:
                h._thread.join(timeout=timeout)
        return handles

    def get_status(self, agent_id: str) -> dict | None:
        with self._lock:
            handle = self._agents.get(agent_id)
        if not handle:
            return None
        return {
            "id": handle.id, "type": handle.agent_type, "task": handle.task,
            "status": handle.status, "age": time.time() - handle.created_at,
            "has_result": bool(handle.result),
        }

    def get_result(self, agent_id: str) -> str:
        with self._lock:
            handle = self._agents.get(agent_id)
        return handle.result if handle else ""

    def kill_agent(self, agent_id: str) -> bool:
        with self._lock:
            handle = self._agents.get(agent_id)
        if not handle:
            return False
        handle.status = "cancelled"
        return True

    def list_running(self) -> list[dict]:
        with self._lock:
            return [
                {"id": h.id, "type": h.agent_type, "task": h.task[:60], "status": h.status}
                for h in self._agents.values()
                if h.status in ("pending", "running")
            ]

    def list_all(self) -> list[dict]:
        with self._lock:
            return [
                {"id": h.id, "type": h.agent_type, "task": h.task[:60],
                 "status": h.status, "age": time.time() - h.created_at}
                for h in self._agents.values()
            ]

    def cleanup(self, max_age: float = 3600):
        now = time.time()
        with self._lock:
            to_remove = [
                aid for aid, h in self._agents.items()
                if h.status in ("done", "failed", "cancelled") and (now - h.created_at) > max_age
            ]
            for aid in to_remove:
                del self._agents[aid]
