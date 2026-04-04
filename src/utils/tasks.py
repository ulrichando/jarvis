"""
Task management for agent swarms.

Provides file-based task list management with locking for concurrent access.
"""

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional, Set

logger = logging.getLogger(__name__)

# Task statuses
TASK_STATUSES = ("pending", "in_progress", "completed")
TaskStatus = Literal["pending", "in_progress", "completed"]

# High water mark file name
HIGH_WATER_MARK_FILE = ".highwatermark"


@dataclass
class Task:
    id: str
    subject: str
    description: str
    status: TaskStatus
    blocks: List[str] = field(default_factory=list)
    blocked_by: List[str] = field(default_factory=list)
    active_form: Optional[str] = None  # present continuous form for spinner
    owner: Optional[str] = None  # agent ID
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class ClaimTaskResult:
    success: bool
    reason: Optional[str] = None  # task_not_found, already_claimed, already_resolved, blocked, agent_busy
    task: Optional[Task] = None
    busy_with_tasks: Optional[List[str]] = None
    blocked_by_tasks: Optional[List[str]] = None


@dataclass
class TeamMember:
    agent_id: str
    name: str
    agent_type: Optional[str] = None


@dataclass
class AgentStatus:
    agent_id: str
    name: str
    status: Literal["idle", "busy"]
    current_tasks: List[str] = field(default_factory=list)
    agent_type: Optional[str] = None


@dataclass
class UnassignTasksResult:
    unassigned_tasks: List[Dict[str, str]]
    notification_message: str


# Listeners for task list updates
_task_update_callbacks: List[Callable[[], None]] = []
_leader_team_name: Optional[str] = None

DEFAULT_TASKS_MODE_TASK_LIST_ID = "tasklist"


def set_leader_team_name(team_name: str) -> None:
    """Sets the leader's team name for task list resolution."""
    global _leader_team_name
    if _leader_team_name == team_name:
        return
    _leader_team_name = team_name
    notify_tasks_updated()


def clear_leader_team_name() -> None:
    """Clears the leader's team name."""
    global _leader_team_name
    if _leader_team_name is None:
        return
    _leader_team_name = None
    notify_tasks_updated()


def on_tasks_updated(callback: Callable[[], None]) -> Callable[[], None]:
    """Register a listener for task updates. Returns unsubscribe function."""
    _task_update_callbacks.append(callback)

    def unsubscribe():
        if callback in _task_update_callbacks:
            _task_update_callbacks.remove(callback)

    return unsubscribe


def notify_tasks_updated() -> None:
    """Notify listeners that tasks have been updated."""
    for cb in _task_update_callbacks:
        try:
            cb()
        except Exception:
            pass


def sanitize_path_component(input_str: str) -> str:
    """Sanitizes a string for safe use in file paths."""
    return re.sub(r"[^a-zA-Z0-9_-]", "-", input_str)


def _get_config_home() -> str:
    """Get the JARVIS config home directory."""
    return os.environ.get("JARVIS_HOME", os.path.expanduser("~/.jarvis"))


def get_tasks_dir(task_list_id: str) -> str:
    return os.path.join(_get_config_home(), "tasks", sanitize_path_component(task_list_id))


def get_task_path(task_list_id: str, task_id: str) -> str:
    return os.path.join(get_tasks_dir(task_list_id), f"{sanitize_path_component(task_id)}.json")


async def ensure_tasks_dir(task_list_id: str) -> None:
    dir_path = get_tasks_dir(task_list_id)
    os.makedirs(dir_path, exist_ok=True)


def _get_high_water_mark_path(task_list_id: str) -> str:
    return os.path.join(get_tasks_dir(task_list_id), HIGH_WATER_MARK_FILE)


async def _read_high_water_mark(task_list_id: str) -> int:
    path = _get_high_water_mark_path(task_list_id)
    try:
        with open(path, "r") as f:
            content = f.read().strip()
            return int(content) if content.isdigit() else 0
    except (FileNotFoundError, ValueError):
        return 0


async def _write_high_water_mark(task_list_id: str, value: int) -> None:
    path = _get_high_water_mark_path(task_list_id)
    with open(path, "w") as f:
        f.write(str(value))


def _task_from_dict(data: Dict[str, Any]) -> Optional[Task]:
    """Parse a task dict into a Task object."""
    try:
        return Task(
            id=data["id"],
            subject=data["subject"],
            description=data["description"],
            status=data["status"],
            blocks=data.get("blocks", []),
            blocked_by=data.get("blockedBy", data.get("blocked_by", [])),
            active_form=data.get("activeForm", data.get("active_form")),
            owner=data.get("owner"),
            metadata=data.get("metadata"),
        )
    except (KeyError, TypeError):
        return None


def _task_to_dict(task: Task) -> Dict[str, Any]:
    """Convert a Task to a serializable dict."""
    d: Dict[str, Any] = {
        "id": task.id,
        "subject": task.subject,
        "description": task.description,
        "status": task.status,
        "blocks": task.blocks,
        "blockedBy": task.blocked_by,
    }
    if task.active_form is not None:
        d["activeForm"] = task.active_form
    if task.owner is not None:
        d["owner"] = task.owner
    if task.metadata is not None:
        d["metadata"] = task.metadata
    return d


async def _find_highest_task_id_from_files(task_list_id: str) -> int:
    dir_path = get_tasks_dir(task_list_id)
    try:
        files = os.listdir(dir_path)
    except FileNotFoundError:
        return 0

    highest = 0
    for f in files:
        if not f.endswith(".json"):
            continue
        try:
            task_id = int(f.replace(".json", ""))
            if task_id > highest:
                highest = task_id
        except ValueError:
            continue
    return highest


async def _find_highest_task_id(task_list_id: str) -> int:
    from_files = await _find_highest_task_id_from_files(task_list_id)
    from_mark = await _read_high_water_mark(task_list_id)
    return max(from_files, from_mark)


async def create_task(task_list_id: str, task_data: Dict[str, Any]) -> str:
    """Creates a new task with a unique ID."""
    await ensure_tasks_dir(task_list_id)
    highest_id = await _find_highest_task_id(task_list_id)
    task_id = str(highest_id + 1)

    task = Task(id=task_id, **task_data)
    path = get_task_path(task_list_id, task_id)
    with open(path, "w") as f:
        json.dump(_task_to_dict(task), f, indent=2)

    notify_tasks_updated()
    return task_id


async def get_task(task_list_id: str, task_id: str) -> Optional[Task]:
    """Get a task by ID."""
    path = get_task_path(task_list_id, task_id)
    try:
        with open(path, "r") as f:
            data = json.load(f)
        return _task_from_dict(data)
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, Exception) as e:
        logger.debug(f"[Tasks] Failed to read task {task_id}: {e}")
        return None


async def update_task(
    task_list_id: str,
    task_id: str,
    updates: Dict[str, Any],
) -> Optional[Task]:
    """Update a task's fields."""
    existing = await get_task(task_list_id, task_id)
    if not existing:
        return None

    for key, value in updates.items():
        if hasattr(existing, key):
            setattr(existing, key, value)
        # Handle camelCase -> snake_case mapping
        snake_key = re.sub(r"([A-Z])", r"_\1", key).lower()
        if hasattr(existing, snake_key):
            setattr(existing, snake_key, value)

    path = get_task_path(task_list_id, task_id)
    with open(path, "w") as f:
        json.dump(_task_to_dict(existing), f, indent=2)

    notify_tasks_updated()
    return existing


async def delete_task(task_list_id: str, task_id: str) -> bool:
    """Delete a task and remove references from other tasks."""
    path = get_task_path(task_list_id, task_id)
    try:
        # Update high water mark before deleting
        try:
            numeric_id = int(task_id)
            current_mark = await _read_high_water_mark(task_list_id)
            if numeric_id > current_mark:
                await _write_high_water_mark(task_list_id, numeric_id)
        except ValueError:
            pass

        os.unlink(path)

        # Remove references from other tasks
        all_tasks = await list_tasks(task_list_id)
        for task in all_tasks:
            new_blocks = [b for b in task.blocks if b != task_id]
            new_blocked_by = [b for b in task.blocked_by if b != task_id]
            if len(new_blocks) != len(task.blocks) or len(new_blocked_by) != len(task.blocked_by):
                await update_task(task_list_id, task.id, {
                    "blocks": new_blocks,
                    "blocked_by": new_blocked_by,
                })

        notify_tasks_updated()
        return True
    except FileNotFoundError:
        return False
    except Exception:
        return False


async def list_tasks(task_list_id: str) -> List[Task]:
    """List all tasks in a task list."""
    dir_path = get_tasks_dir(task_list_id)
    try:
        files = os.listdir(dir_path)
    except FileNotFoundError:
        return []

    task_ids = [f.replace(".json", "") for f in files if f.endswith(".json")]
    tasks = []
    for tid in task_ids:
        task = await get_task(task_list_id, tid)
        if task is not None:
            tasks.append(task)
    return tasks


async def block_task(task_list_id: str, from_task_id: str, to_task_id: str) -> bool:
    """Set task dependency: from_task blocks to_task."""
    from_task = await get_task(task_list_id, from_task_id)
    to_task = await get_task(task_list_id, to_task_id)
    if not from_task or not to_task:
        return False

    if to_task_id not in from_task.blocks:
        await update_task(task_list_id, from_task_id, {
            "blocks": [*from_task.blocks, to_task_id],
        })

    if from_task_id not in to_task.blocked_by:
        await update_task(task_list_id, to_task_id, {
            "blocked_by": [*to_task.blocked_by, from_task_id],
        })

    return True


async def claim_task(
    task_list_id: str,
    task_id: str,
    claimant_agent_id: str,
    check_agent_busy: bool = False,
) -> ClaimTaskResult:
    """Attempts to claim a task for an agent."""
    task = await get_task(task_list_id, task_id)
    if not task:
        return ClaimTaskResult(success=False, reason="task_not_found")

    if task.owner and task.owner != claimant_agent_id:
        return ClaimTaskResult(success=False, reason="already_claimed", task=task)

    if task.status == "completed":
        return ClaimTaskResult(success=False, reason="already_resolved", task=task)

    all_tasks = await list_tasks(task_list_id)
    unresolved_ids = {t.id for t in all_tasks if t.status != "completed"}
    blocked_by = [bid for bid in task.blocked_by if bid in unresolved_ids]
    if blocked_by:
        return ClaimTaskResult(success=False, reason="blocked", task=task, blocked_by_tasks=blocked_by)

    if check_agent_busy:
        agent_open = [
            t for t in all_tasks
            if t.status != "completed" and t.owner == claimant_agent_id and t.id != task_id
        ]
        if agent_open:
            return ClaimTaskResult(
                success=False,
                reason="agent_busy",
                task=task,
                busy_with_tasks=[t.id for t in agent_open],
            )

    updated = await update_task(task_list_id, task_id, {"owner": claimant_agent_id})
    return ClaimTaskResult(success=True, task=updated)


async def unassign_teammate_tasks(
    team_name: str,
    teammate_id: str,
    teammate_name: str,
    reason: Literal["terminated", "shutdown"],
) -> UnassignTasksResult:
    """Unassign all open tasks from a teammate."""
    tasks = await list_tasks(team_name)
    unresolved = [
        t for t in tasks
        if t.status != "completed" and (t.owner == teammate_id or t.owner == teammate_name)
    ]

    for task in unresolved:
        await update_task(team_name, task.id, {"owner": None, "status": "pending"})

    action_verb = "was terminated" if reason == "terminated" else "has shut down"
    notification = f"{teammate_name} {action_verb}."
    if unresolved:
        task_list = ", ".join(f'#{t.id} "{t.subject}"' for t in unresolved)
        notification += (
            f" {len(unresolved)} task(s) were unassigned: {task_list}. "
            "Use TaskList to check availability and TaskUpdate with owner to reassign them."
        )

    return UnassignTasksResult(
        unassigned_tasks=[{"id": t.id, "subject": t.subject} for t in unresolved],
        notification_message=notification,
    )


async def reset_task_list(task_list_id: str) -> None:
    """Resets the task list -- clears any existing tasks."""
    await ensure_tasks_dir(task_list_id)
    current_highest = await _find_highest_task_id_from_files(task_list_id)
    if current_highest > 0:
        existing_mark = await _read_high_water_mark(task_list_id)
        if current_highest > existing_mark:
            await _write_high_water_mark(task_list_id, current_highest)

    dir_path = get_tasks_dir(task_list_id)
    try:
        files = os.listdir(dir_path)
    except FileNotFoundError:
        files = []

    for f in files:
        if f.endswith(".json") and not f.startswith("."):
            try:
                os.unlink(os.path.join(dir_path, f))
            except OSError:
                pass

    notify_tasks_updated()
