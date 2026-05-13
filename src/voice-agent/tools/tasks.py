"""Task-list tools — port of claude-code's TaskCreate / TaskGet /
TaskList / TaskUpdate (+ TodoWrite bulk variant).

Lets the supervisor manage a structured task checklist across the
voice session. Storage is one JSON file per task-list:

    ~/.jarvis/voice-tasks/<list-id>/tasks.json

`<list-id>` comes from the `JARVIS_TASK_LIST_ID` env var; defaults to
`"default"` so tasks persist across voice sessions unless the user
explicitly switches lists. (Claude-code's default is per-session,
but voice JARVIS is a continuous companion — a single durable list
matches usage. Set the env var to scope per-feature.)

Schema (JSON):

    {
      "schema_version": 1,
      "next_id": 4,
      "tasks": [
        {
          "id": "1",
          "content": "Clean up bash.py audit nits",
          "active_form": "Cleaning up bash.py audit nits",
          "status": "completed",
          "created": "2026-05-12T22:39:46Z",
          "updated": "2026-05-12T22:55:18Z"
        },
        ...
      ]
    }

Concurrency: fcntl.flock on a sibling .lock file. Multiple voice-
agent workers can race-add safely; reads are lock-free (fast path).

Separated from `~/.jarvis/tasks/` which is claude-code's own task
store — directory + lock formats are intentionally different so the
two don't step on each other.
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

from livekit.agents.llm import function_tool

from pipeline.hooks import fire_hook


__all__ = [
    "TASKS_DIR",
    "task_create",
    "task_list",
    "task_update",
    "task_delete",
    "todo_write",
]


_logger = logging.getLogger("jarvis.tools.tasks")


TASKS_DIR: Path = Path.home() / ".jarvis" / "voice-tasks"

_VALID_STATUSES = ("pending", "in_progress", "completed")


# ── Storage helpers ──────────────────────────────────────────────


def _list_id() -> str:
    """Resolve the active task-list id. `JARVIS_TASK_LIST_ID` env
    overrides; otherwise the shared "default" list."""
    return (os.environ.get("JARVIS_TASK_LIST_ID") or "default").strip()


def _list_dir() -> Path:
    return TASKS_DIR / _list_id()


def _tasks_path() -> Path:
    return _list_dir() / "tasks.json"


def _lock_path() -> Path:
    return _list_dir() / ".lock"


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _empty_state() -> dict:
    return {"schema_version": 1, "next_id": 1, "tasks": []}


def _read_unlocked() -> dict:
    """Read the task file without taking the write-lock. Fast path
    for `task_list` queries that don't mutate."""
    path = _tasks_path()
    if not path.exists():
        return _empty_state()
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        _logger.warning(f"[tasks] read failed ({path}): {e}; treating as empty")
        return _empty_state()
    if not isinstance(data, dict) or "tasks" not in data:
        return _empty_state()
    return data


def _mutate(fn) -> Any:
    """Run `fn(state) -> (new_state, return_value)` under flock.

    Creates the list directory + lock file on first use. The state
    is reread inside the lock so concurrent writers see each other's
    changes. Caller's `fn` must be pure-functional over `state` —
    side effects are this helper's job.
    """
    _list_dir().mkdir(parents=True, exist_ok=True)
    lock_path = _lock_path()
    lock_path.touch(exist_ok=True)
    with lock_path.open("rb") as lf:
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
        try:
            state = _read_unlocked()
            new_state, retval = fn(state)
            tmp = _tasks_path().with_suffix(".json.tmp")
            tmp.write_text(json.dumps(new_state, indent=2), encoding="utf-8")
            tmp.replace(_tasks_path())
            return retval
        finally:
            fcntl.flock(lf.fileno(), fcntl.LOCK_UN)


# ── Validation ──────────────────────────────────────────────────


def _normalise_status(status: Optional[str]) -> Optional[str]:
    if status is None:
        return None
    s = status.strip().lower().replace("-", "_").replace(" ", "_")
    if s in _VALID_STATUSES:
        return s
    raise ValueError(
        f"invalid status {status!r}; expected one of {_VALID_STATUSES}"
    )


def _find_task(state: dict, task_id: str) -> Optional[dict]:
    tid = str(task_id).strip()
    for t in state.get("tasks", []):
        if str(t.get("id")) == tid:
            return t
    return None


# ── @function_tool surface ──────────────────────────────────────


@function_tool
async def task_create(content: str, active_form: str = "") -> str:
    """Create a new task in the current task list.

    Call this when the user assigns a multi-step piece of work, or
    when you (the supervisor) decide a complex request warrants
    explicit tracking. New tasks start in 'pending' status.

    Args:
        content: Imperative description ("Build the auth flow").
        active_form: Present-continuous form shown while the task is
                     in_progress ("Building the auth flow"). Optional;
                     if omitted, the content is used in both places.

    Returns:
        One-line confirmation including the new task id.
    """
    c = (content or "").strip()
    if not c:
        return "Task content can't be empty — pass a short imperative description."
    af = (active_form or "").strip() or c

    def _add(state: dict):
        next_id = int(state.get("next_id", 1))
        task = {
            "id": str(next_id),
            "content": c,
            "active_form": af,
            "status": "pending",
            "created": _now_iso(),
            "updated": _now_iso(),
        }
        state = {**state, "next_id": next_id + 1,
                 "tasks": list(state.get("tasks", [])) + [task]}
        return state, task["id"]

    new_id = _mutate(_add)
    await fire_hook("task_created", {
        "task_id": new_id, "content": c, "active_form": af, "list_id": _list_id(),
    })
    return f"Task #{new_id} created: {c[:100]}"


@function_tool
async def task_list(status_filter: str = "") -> str:
    """List tasks in the current task list, optionally filtered.

    Call this when the user asks "what's on my plate" / "what tasks
    do I have" / "show my todo" / "what's next". Returns a numbered
    rundown grouped by status.

    Args:
        status_filter: Optional status to filter by — one of 'pending',
                       'in_progress', 'completed'. Empty / omitted →
                       all statuses.
    """
    try:
        wanted = _normalise_status(status_filter or None)
    except ValueError as e:
        return f"Bad status filter: {e}. Use 'pending' / 'in_progress' / 'completed' or leave empty."

    state = _read_unlocked()
    tasks = state.get("tasks", [])
    if wanted:
        tasks = [t for t in tasks if t.get("status") == wanted]
    if not tasks:
        if wanted:
            return f"No tasks with status {wanted!r}."
        return "Task list is empty."

    in_progress = [t for t in tasks if t.get("status") == "in_progress"]
    pending     = [t for t in tasks if t.get("status") == "pending"]
    completed   = [t for t in tasks if t.get("status") == "completed"]

    lines: list[str] = []
    if in_progress:
        lines.append(f"In progress ({len(in_progress)}):")
        for t in in_progress:
            lines.append(f"  #{t['id']} {t.get('active_form') or t.get('content')}")
    if pending:
        lines.append(f"Pending ({len(pending)}):")
        for t in pending:
            lines.append(f"  #{t['id']} {t.get('content')}")
    if completed:
        lines.append(f"Completed ({len(completed)}):")
        for t in completed:
            lines.append(f"  #{t['id']} {t.get('content')}")
    return "\n".join(lines)


@function_tool
async def task_update(
    task_id: str, status: str = "", content: str = "", active_form: str = "",
) -> str:
    """Update one task's status or content.

    Common transitions: pending → in_progress (when starting work),
    in_progress → completed (when finishing). Per the claude-code
    discipline that JARVIS mirrors, exactly ONE task should be
    in_progress at a time.

    Args:
        task_id:     The numeric task id from task_list (no '#').
        status:      New status: 'pending' / 'in_progress' / 'completed'.
                     Empty / omitted → no status change.
        content:     New imperative content. Empty → no content change.
        active_form: New present-continuous form. Empty → no change.

    Returns:
        One-line confirmation of what changed.
    """
    try:
        new_status = _normalise_status(status or None)
    except ValueError as e:
        return f"Bad status: {e}."

    new_content = (content or "").strip() or None
    new_active = (active_form or "").strip() or None

    completed_now = {"flag": False, "content": ""}

    def _mut(state: dict):
        task = _find_task(state, task_id)
        if task is None:
            return state, f"Task #{task_id} not found. Call task_list to see active ids."
        changes: list[str] = []
        if new_status and task.get("status") != new_status:
            changes.append(f"status: {task.get('status')} → {new_status}")
            if new_status == "completed":
                completed_now["flag"] = True
                completed_now["content"] = task.get("content", "")
            task["status"] = new_status
        if new_content and task.get("content") != new_content:
            changes.append(f"content updated")
            task["content"] = new_content
        if new_active and task.get("active_form") != new_active:
            changes.append("active_form updated")
            task["active_form"] = new_active
        if not changes:
            return state, f"Task #{task_id}: nothing to change."
        task["updated"] = _now_iso()
        return state, f"Task #{task_id}: " + ", ".join(changes) + "."

    result = _mutate(_mut)
    if completed_now["flag"]:
        await fire_hook("task_completed", {
            "task_id": str(task_id).strip(),
            "content": completed_now["content"],
            "list_id": _list_id(),
        })
    return result


@function_tool
async def task_delete(task_id: str) -> str:
    """Delete a task from the list. Use only for retracted /
    cancelled items — for finished work, call task_update(status=
    'completed') instead so the history is preserved.

    Args:
        task_id: The numeric task id from task_list (no '#').
    """
    def _mut(state: dict):
        before = state.get("tasks", [])
        target = _find_task(state, task_id)
        if target is None:
            return state, f"Task #{task_id} not found."
        after = [t for t in before if str(t.get("id")) != str(task_id).strip()]
        state = {**state, "tasks": after}
        return state, f"Task #{task_id} removed: {target.get('content', '')[:80]}"

    return _mutate(_mut)


@function_tool
async def todo_write(todos_json: str) -> str:
    """Bulk-replace the entire task list with a new array.

    Mirror of claude-code's TodoWrite — use when you (the supervisor)
    want to set the whole list from a fresh plan in one call instead
    of N task_create calls. Preserves ids when possible (matching by
    content); generates new ids for new entries; removes anything
    absent from the input.

    Args:
        todos_json: A JSON string of the form
            [{"content": "...", "active_form": "...", "status": "pending"}, ...]
            'status' is optional (defaults to 'pending'). 'active_form'
            is optional (defaults to 'content'). Order is preserved.

    Returns:
        Summary of how the list changed.
    """
    try:
        parsed = json.loads(todos_json)
    except json.JSONDecodeError as e:
        return f"Bad JSON: {e}. Pass a JSON array of task objects."
    if not isinstance(parsed, list):
        return "Bad shape: top-level must be a JSON array."

    incoming: list[dict] = []
    for i, item in enumerate(parsed):
        if not isinstance(item, dict):
            return f"Bad item {i}: each entry must be an object."
        content = str(item.get("content") or "").strip()
        if not content:
            return f"Bad item {i}: missing 'content'."
        try:
            status = _normalise_status(item.get("status") or "pending") or "pending"
        except ValueError as e:
            return f"Bad item {i}: {e}."
        active_form = str(item.get("active_form") or content).strip()
        incoming.append({"content": content, "active_form": active_form, "status": status})

    def _mut(state: dict):
        old_by_content = {t.get("content"): t for t in state.get("tasks", [])}
        next_id = int(state.get("next_id", 1))
        new_tasks: list[dict] = []
        kept = added = 0
        for item in incoming:
            existing = old_by_content.get(item["content"])
            if existing is not None:
                merged = {**existing, **item, "updated": _now_iso()}
                new_tasks.append(merged)
                kept += 1
            else:
                new_tasks.append({
                    "id": str(next_id),
                    "content": item["content"],
                    "active_form": item["active_form"],
                    "status": item["status"],
                    "created": _now_iso(),
                    "updated": _now_iso(),
                })
                next_id += 1
                added += 1
        removed = len(state.get("tasks", [])) - kept
        state = {**state, "next_id": next_id, "tasks": new_tasks}
        return state, (
            f"Task list replaced. {kept} kept, {added} added, "
            f"{removed} removed (now {len(new_tasks)} total)."
        )

    return _mutate(_mut)
