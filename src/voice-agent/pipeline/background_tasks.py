"""In-process registry + completion-announcement queue for fire-and-forget
background tasks.

The supervisor kicks a long task off via ``dispatch_agent(..., background=True)``;
the tool returns immediately (so the turn never blocks and the user keeps
talking) and spawns an asyncio runner. When the runner finishes it drops a
spoken announcement here via :func:`complete`. The voice agent's
``_background_task_watcher`` (jarvis_agent.py) polls :func:`drain_announcements`
every :data:`POLL_S` seconds and voices each one with ``session.say()`` — the
same delivery rail the cron pending-watcher uses, but in-process and with
background-appropriate wording.

Concurrency: everything runs on the single voice-agent event loop, so the
runner task and the watcher task never execute simultaneously. Plain
list/dict mutation between awaits is therefore atomic — no lock needed (this
mirrors the deliberately-simple cron_delivery design, minus the cross-process
file because background tasks live and die inside one worker process).
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List

logger = logging.getLogger("jarvis.background_tasks")

# All env-derived knobs are read at runtime (not import time) so operator
# tuning + tests take effect without a re-import — same convention as the
# memory consolidator.

def poll_s() -> float:
    """How often the in-session watcher re-checks for finished background
    tasks. 3 s keeps "tell me when it's done" snappy without a busy loop (the
    check is a single list-truthiness test when nothing is pending)."""
    try:
        return float(os.environ.get("JARVIS_BG_TASK_POLL_S", "3"))
    except ValueError:
        return 3.0


def max_concurrent() -> int:
    """Max concurrently-running background tasks. A safety cap so a misbehaving
    supervisor (or prompt injection) can't fork an unbounded number of
    bin/jarvis subprocesses. When exceeded, dispatch_agent refuses politely."""
    try:
        return int(os.environ.get("JARVIS_BG_TASK_MAX", "3"))
    except ValueError:
        return 3

# task_id -> {"id", "description", "started_at", "status", "finished_at"}
_tasks: Dict[str, Dict[str, Any]] = {}

# FIFO queue of spoken announcements awaiting the watcher.
_pending: List[str] = []


def register(task_id: str, description: str) -> None:
    """Record a newly-spawned background task as running."""
    _tasks[task_id] = {
        "id": task_id,
        "description": description or task_id,
        "started_at": time.time(),
        "status": "running",
        "finished_at": None,
    }
    logger.info("[bg] register id=%s desc=%r (%d active)",
                task_id, description, len(active()))


# Keep at most this many finished tasks in `_tasks`. active() only reads
# running tasks, so finished entries serve no purpose beyond near-term
# status reporting — without a cap the dict grows for the life of the
# process (memory_extractor spawns bg work every turn boundary).
_FINISHED_RETENTION = 50


def _prune_finished() -> None:
    """Evict the oldest finished tasks once they exceed the retention cap."""
    finished = [
        (tid, t) for tid, t in _tasks.items() if t.get("status") != "running"
    ]
    if len(finished) <= _FINISHED_RETENTION:
        return
    finished.sort(key=lambda kv: kv[1].get("finished_at") or 0.0)
    for tid, _t in finished[: len(finished) - _FINISHED_RETENTION]:
        _tasks.pop(tid, None)


def complete(task_id: str, announcement: str | None, status: str = "success") -> None:
    """Mark a task finished and enqueue its spoken announcement (if any).

    ``announcement`` is the voice-friendly sentence the watcher will speak.
    Pass ``None`` to finish a task silently (e.g. nothing worth voicing).
    """
    t = _tasks.get(task_id)
    if t is not None:
        t["status"] = status
        t["finished_at"] = time.time()
    if announcement:
        _pending.append(announcement)
    logger.info("[bg] complete id=%s status=%s voiced=%s",
                task_id, status, bool(announcement))
    _prune_finished()


def discard(task_id: str) -> None:
    """Drop a task without voicing anything (e.g. cancelled on shutdown)."""
    _tasks.pop(task_id, None)
    logger.info("[bg] discard id=%s", task_id)


def drain_announcements() -> List[str]:
    """Pop + return all pending announcements (the watcher voices them)."""
    if not _pending:
        return []
    items = _pending[:]
    _pending.clear()
    return items


def requeue(announcement: str) -> None:
    """Put an announcement back at the FRONT of the queue — used by the
    watcher when ``session.say`` wasn't ready, so it's retried next tick."""
    _pending.insert(0, announcement)


def active() -> List[Dict[str, Any]]:
    """Currently-running background tasks (newest registrations included)."""
    return [t for t in _tasks.values() if t.get("status") == "running"]


def active_count() -> int:
    return len(active())


def reset() -> None:
    """Test helper — clear all state."""
    _tasks.clear()
    _pending.clear()
