"""JARVIS Agent Coordinator — thread-based multi-agent swarm.

Supports:
- parallel: Multiple agents work on DIFFERENT subtasks simultaneously
- sequential: Each agent builds on previous output
- pipeline: Scout → Planner → Worker chain
- swarm: Planner decomposes task → N workers execute in parallel → Merger combines results

Agents run on OS threads for isolation. Each gets its own event loop.
"""

import threading
import asyncio
import uuid
import json
import time
import logging
from dataclasses import dataclass, field

log = logging.getLogger("jarvis.coordinator")


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

    def __init__(self):
        self._agents: dict[str, AgentHandle] = {}
        self._lock = threading.Lock()

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
