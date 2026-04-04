"""Scheduled prompt tasks stored in .claude/scheduled_tasks.json."""

from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from .cronJitterConfig import CronJitterConfig, DEFAULT_CRON_JITTER_CONFIG

logger = logging.getLogger(__name__)

CRON_FILE_REL = os.path.join(".claude", "scheduled_tasks.json")


@dataclass
class CronTask:
    id: str
    cron: str
    prompt: str
    created_at: int
    last_fired_at: Optional[int] = None
    recurring: bool = False
    permanent: bool = False
    durable: Optional[bool] = None
    agent_id: Optional[str] = None


def get_cron_file_path(directory: Optional[str] = None) -> str:
    """Get the path to the cron tasks file."""
    if directory is None:
        directory = os.getcwd()
    return os.path.join(directory, CRON_FILE_REL)


def has_cron_tasks_sync(directory: Optional[str] = None) -> bool:
    """Check if cron tasks file exists and has tasks."""
    path = get_cron_file_path(directory)
    try:
        with open(path) as f:
            data = json.load(f)
        return bool(data.get("tasks"))
    except (FileNotFoundError, json.JSONDecodeError):
        return False


async def read_cron_tasks(directory: Optional[str] = None) -> list[CronTask]:
    """Read cron tasks from the file."""
    path = get_cron_file_path(directory)
    try:
        with open(path) as f:
            data = json.load(f)
        tasks = []
        for t in data.get("tasks", []):
            tasks.append(CronTask(
                id=t["id"],
                cron=t["cron"],
                prompt=t["prompt"],
                created_at=t["createdAt"],
                last_fired_at=t.get("lastFiredAt"),
                recurring=t.get("recurring", False),
                permanent=t.get("permanent", False),
            ))
        return tasks
    except (FileNotFoundError, json.JSONDecodeError):
        return []


async def write_cron_tasks(
    tasks: list[CronTask], directory: Optional[str] = None
) -> None:
    """Write cron tasks to the file."""
    path = get_cron_file_path(directory)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = {
        "tasks": [
            {
                "id": t.id,
                "cron": t.cron,
                "prompt": t.prompt,
                "createdAt": t.created_at,
                **({"lastFiredAt": t.last_fired_at} if t.last_fired_at else {}),
                **({"recurring": True} if t.recurring else {}),
                **({"permanent": True} if t.permanent else {}),
            }
            for t in tasks
        ]
    }
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


async def remove_cron_tasks(
    task_ids: list[str], directory: Optional[str] = None
) -> None:
    """Remove specific cron tasks by ID."""
    tasks = await read_cron_tasks(directory)
    remaining = [t for t in tasks if t.id not in task_ids]
    await write_cron_tasks(remaining, directory)


async def mark_cron_tasks_fired(
    task_ids: list[str], fired_at: int, directory: Optional[str] = None
) -> None:
    """Mark cron tasks as fired."""
    tasks = await read_cron_tasks(directory)
    for task in tasks:
        if task.id in task_ids:
            task.last_fired_at = fired_at
    await write_cron_tasks(tasks, directory)


def find_missed_tasks(tasks: list[CronTask], now_ms: int) -> list[CronTask]:
    """Find tasks that should have fired but were missed."""
    return []  # Simplified


def jittered_next_cron_run_ms(
    task: CronTask, now_ms: int, config: CronJitterConfig = DEFAULT_CRON_JITTER_CONFIG
) -> int:
    """Calculate jittered next run time."""
    return now_ms + 60_000  # Simplified: 1 minute


def one_shot_jittered_next_cron_run_ms(
    task: CronTask, now_ms: int, config: CronJitterConfig = DEFAULT_CRON_JITTER_CONFIG
) -> int:
    """Calculate jittered next run time for one-shot tasks."""
    return now_ms + 60_000
