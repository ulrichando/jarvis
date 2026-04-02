"""JARVIS Background Runner — execute tasks asynchronously.

Supports:
- One-shot background tasks
- Scheduled recurring tasks (simple cron-like)
- Task status tracking
"""

import asyncio
import time
import uuid
import logging
from dataclasses import dataclass, field

log = logging.getLogger("jarvis.runner")


@dataclass
class BackgroundTask:
    id: str
    name: str
    status: str = "running"  # running, done, failed, cancelled
    started_at: float = field(default_factory=time.time)
    result: str = ""
    _task: asyncio.Task | None = field(default=None, repr=False)


@dataclass
class ScheduledTask:
    id: str
    name: str
    interval_seconds: float
    _running: bool = False
    _task: asyncio.Task | None = field(default=None, repr=False)


class BackgroundRunner:
    """Runs tasks in background asyncio tasks."""

    def __init__(self):
        self._tasks: dict[str, BackgroundTask] = {}
        self._scheduled: dict[str, ScheduledTask] = {}

    def run(self, name: str, coroutine) -> str:
        """Run a coroutine in the background. Returns task ID."""
        task_id = uuid.uuid4().hex[:8]
        bg = BackgroundTask(id=task_id, name=name)

        async def wrapper():
            try:
                result = await coroutine
                bg.result = str(result) if result else "completed"
                bg.status = "done"
                log.info("Background task %s completed", task_id)
            except asyncio.CancelledError:
                bg.status = "cancelled"
            except Exception as e:
                bg.result = str(e)
                bg.status = "failed"
                log.error("Background task %s failed: %s", task_id, e)

        bg._task = asyncio.create_task(wrapper())
        self._tasks[task_id] = bg
        return task_id

    def cancel(self, task_id: str) -> bool:
        """Cancel a running task."""
        bg = self._tasks.get(task_id)
        if bg and bg._task and bg.status == "running":
            bg._task.cancel()
            bg.status = "cancelled"
            return True
        # Check scheduled
        sched = self._scheduled.get(task_id)
        if sched and sched._task:
            sched._running = False
            sched._task.cancel()
            del self._scheduled[task_id]
            return True
        return False

    def status(self, task_id: str) -> dict | None:
        bg = self._tasks.get(task_id)
        if bg:
            return {"id": bg.id, "name": bg.name, "status": bg.status,
                    "age": time.time() - bg.started_at, "result": bg.result[:200]}
        sched = self._scheduled.get(task_id)
        if sched:
            return {"id": sched.id, "name": sched.name, "type": "scheduled",
                    "interval": sched.interval_seconds, "running": sched._running}
        return None

    def list_running(self) -> list[dict]:
        result = []
        for bg in self._tasks.values():
            if bg.status == "running":
                result.append({"id": bg.id, "name": bg.name, "age": time.time() - bg.started_at})
        for sched in self._scheduled.values():
            result.append({"id": sched.id, "name": sched.name, "type": "scheduled",
                           "interval": sched.interval_seconds})
        return result

    def list_all(self) -> list[dict]:
        result = []
        for bg in self._tasks.values():
            result.append({"id": bg.id, "name": bg.name, "status": bg.status,
                           "age": time.time() - bg.started_at})
        return result

    def schedule(self, name: str, interval_seconds: float, coro_factory) -> str:
        """Schedule a recurring task. coro_factory is called each iteration to get a new coroutine."""
        task_id = uuid.uuid4().hex[:8]
        sched = ScheduledTask(id=task_id, name=name, interval_seconds=interval_seconds, _running=True)

        async def loop():
            while sched._running:
                try:
                    coro = coro_factory()
                    await coro
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    log.error("Scheduled task %s error: %s", task_id, e)
                await asyncio.sleep(interval_seconds)

        sched._task = asyncio.create_task(loop())
        self._scheduled[task_id] = sched
        log.info("Scheduled task %s every %ds", task_id, interval_seconds)
        return task_id

    def cleanup(self, max_age: float = 3600):
        """Remove completed tasks older than max_age."""
        now = time.time()
        to_remove = [
            tid for tid, t in self._tasks.items()
            if t.status in ("done", "failed", "cancelled") and (now - t.started_at) > max_age
        ]
        for tid in to_remove:
            del self._tasks[tid]
