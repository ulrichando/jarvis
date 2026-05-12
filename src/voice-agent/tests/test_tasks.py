"""Tests for `tools/tasks.py` — voice-side task-list tools.

Ported from claude-code's Task* family (TaskCreate / TaskGet /
TaskList / TaskUpdate + TodoWrite bulk variant), see PROMPT in
`~/Documents/Projects/claude-code/src/tools/TodoWriteTool/prompt.ts`.

Each test isolates `TASKS_DIR` to tmp_path so the user's real
~/.jarvis/voice-tasks/ store is never touched.
"""
from __future__ import annotations

import asyncio
import json

import pytest


@pytest.fixture
def tasks_module(tmp_path, monkeypatch):
    """Fresh task-list pointed at tmp_path."""
    from tools import tasks
    monkeypatch.setattr(tasks, "TASKS_DIR", tmp_path / "voice-tasks")
    monkeypatch.delenv("JARVIS_TASK_LIST_ID", raising=False)
    return tasks


def _unwrap(tool):
    """Get the raw coroutine fn out of livekit's @function_tool wrapper."""
    for attr in ("__livekit_agents_func", "_func", "fnc", "func", "callable"):
        f = getattr(tool, attr, None)
        if callable(f):
            return f
    if callable(tool):
        return tool
    raise RuntimeError(f"can't unwrap {tool!r}")


def _run(tool, **kwargs):
    return asyncio.run(_unwrap(tool)(**kwargs))


# ── task_create ──────────────────────────────────────────────────


def test_create_first_task_returns_id_1(tasks_module):
    out = _run(tasks_module.task_create, content="Build the auth flow")
    assert "Task #1 created" in out
    assert "Build the auth flow" in out


def test_create_persists_to_disk(tasks_module, tmp_path):
    _run(tasks_module.task_create, content="First")
    _run(tasks_module.task_create, content="Second")
    state_path = tmp_path / "voice-tasks" / "default" / "tasks.json"
    assert state_path.exists()
    state = json.loads(state_path.read_text())
    assert state["next_id"] == 3
    assert len(state["tasks"]) == 2
    assert state["tasks"][0]["id"] == "1"
    assert state["tasks"][1]["id"] == "2"
    assert state["tasks"][0]["status"] == "pending"


def test_create_uses_content_as_active_form_when_omitted(tasks_module):
    _run(tasks_module.task_create, content="Do X")
    state = json.loads((tasks_module.TASKS_DIR / "default" / "tasks.json").read_text())
    assert state["tasks"][0]["active_form"] == "Do X"


def test_create_active_form_explicit(tasks_module):
    _run(tasks_module.task_create, content="Run tests", active_form="Running tests")
    state = json.loads((tasks_module.TASKS_DIR / "default" / "tasks.json").read_text())
    assert state["tasks"][0]["active_form"] == "Running tests"


def test_create_empty_content_rejected(tasks_module):
    out = _run(tasks_module.task_create, content="   ")
    assert "can't be empty" in out


# ── task_list ───────────────────────────────────────────────────


def test_list_empty(tasks_module):
    assert _run(tasks_module.task_list) == "Task list is empty."


def test_list_groups_by_status(tasks_module):
    _run(tasks_module.task_create, content="Pending A")
    _run(tasks_module.task_create, content="In progress B", active_form="Doing B")
    _run(tasks_module.task_update, task_id="2", status="in_progress")
    _run(tasks_module.task_create, content="Done C")
    _run(tasks_module.task_update, task_id="3", status="completed")

    out = _run(tasks_module.task_list)
    # In progress section appears first (most important)
    assert out.index("In progress (1):") < out.index("Pending (1):")
    assert out.index("Pending (1):") < out.index("Completed (1):")
    assert "Doing B" in out
    assert "Pending A" in out
    assert "Done C" in out


def test_list_status_filter(tasks_module):
    _run(tasks_module.task_create, content="Alpha")
    _run(tasks_module.task_create, content="Beta")
    _run(tasks_module.task_update, task_id="1", status="in_progress")
    out = _run(tasks_module.task_list, status_filter="pending")
    assert "Beta" in out
    assert "Alpha" not in out


def test_list_status_filter_accepts_hyphen_and_space(tasks_module):
    """`in-progress` and `in progress` should both normalise to
    `in_progress` — STT often produces hyphens or spaces."""
    _run(tasks_module.task_create, content="X")
    _run(tasks_module.task_update, task_id="1", status="in_progress")
    for variant in ("in_progress", "in-progress", "In Progress"):
        out = _run(tasks_module.task_list, status_filter=variant)
        assert "X" in out, f"variant {variant!r} did not match"


def test_list_rejects_bad_filter(tasks_module):
    out = _run(tasks_module.task_list, status_filter="nope")
    assert "Bad status filter" in out


# ── task_update ─────────────────────────────────────────────────


def test_update_status_transition(tasks_module):
    _run(tasks_module.task_create, content="Do thing")
    out = _run(tasks_module.task_update, task_id="1", status="in_progress")
    assert "pending -> in_progress" in out or "pending → in_progress" in out


def test_update_content_changes(tasks_module):
    _run(tasks_module.task_create, content="Old")
    out = _run(tasks_module.task_update, task_id="1", content="New")
    assert "content updated" in out
    state = json.loads((tasks_module.TASKS_DIR / "default" / "tasks.json").read_text())
    assert state["tasks"][0]["content"] == "New"


def test_update_missing_id(tasks_module):
    out = _run(tasks_module.task_update, task_id="99", status="completed")
    assert "not found" in out


def test_update_no_changes_returns_friendly_message(tasks_module):
    _run(tasks_module.task_create, content="X")
    out = _run(tasks_module.task_update, task_id="1")
    assert "nothing to change" in out


def test_update_bad_status_rejected(tasks_module):
    _run(tasks_module.task_create, content="X")
    out = _run(tasks_module.task_update, task_id="1", status="zombie")
    assert "Bad status" in out


# ── task_delete ────────────────────────────────────────────────


def test_delete_removes_task(tasks_module):
    _run(tasks_module.task_create, content="A")
    _run(tasks_module.task_create, content="B")
    out = _run(tasks_module.task_delete, task_id="1")
    assert "removed" in out

    state = json.loads((tasks_module.TASKS_DIR / "default" / "tasks.json").read_text())
    assert len(state["tasks"]) == 1
    assert state["tasks"][0]["id"] == "2"


def test_delete_missing_id(tasks_module):
    out = _run(tasks_module.task_delete, task_id="99")
    assert "not found" in out


def test_delete_does_not_reuse_id(tasks_module):
    """After deleting #1 and creating a new task, the new task gets
    a fresh id — not #1 (which would be confusing to the supervisor
    that just heard 'Task #1 removed')."""
    _run(tasks_module.task_create, content="A")
    _run(tasks_module.task_delete, task_id="1")
    out = _run(tasks_module.task_create, content="B")
    assert "Task #2" in out


# ── todo_write (bulk replace) ──────────────────────────────────


def test_todo_write_creates_new_list(tasks_module):
    todos = json.dumps([
        {"content": "Task one"},
        {"content": "Task two", "status": "in_progress"},
    ])
    out = _run(tasks_module.todo_write, todos_json=todos)
    assert "2 added" in out
    state = json.loads((tasks_module.TASKS_DIR / "default" / "tasks.json").read_text())
    assert len(state["tasks"]) == 2
    assert state["tasks"][1]["status"] == "in_progress"


def test_todo_write_preserves_existing_ids_by_content(tasks_module):
    """Bulk replace should KEEP the id for an existing task whose
    content matches — only status updates. Critical so the supervisor
    can call todo_write multiple times without churning ids the user
    has heard."""
    _run(tasks_module.task_create, content="Stable item")
    _run(tasks_module.task_create, content="Will be removed")

    todos = json.dumps([
        {"content": "Stable item", "status": "completed"},
        {"content": "Brand new"},
    ])
    out = _run(tasks_module.todo_write, todos_json=todos)
    assert "1 kept" in out
    assert "1 added" in out
    assert "1 removed" in out

    state = json.loads((tasks_module.TASKS_DIR / "default" / "tasks.json").read_text())
    stable = next(t for t in state["tasks"] if t["content"] == "Stable item")
    assert stable["id"] == "1", "id must be preserved across replace"
    assert stable["status"] == "completed", "status update must apply"


def test_todo_write_bad_json(tasks_module):
    out = _run(tasks_module.todo_write, todos_json="not json")
    assert "Bad JSON" in out


def test_todo_write_bad_shape(tasks_module):
    out = _run(tasks_module.todo_write, todos_json=json.dumps({"oops": "dict"}))
    assert "Bad shape" in out


def test_todo_write_empty_content_rejected(tasks_module):
    out = _run(tasks_module.todo_write, todos_json=json.dumps([{"content": ""}]))
    assert "missing 'content'" in out


# ── list_id resolution ─────────────────────────────────────────


def test_list_id_env_override(tasks_module, monkeypatch):
    """Setting JARVIS_TASK_LIST_ID points storage at a different dir —
    supports multiple persistent lists scoped to features."""
    monkeypatch.setenv("JARVIS_TASK_LIST_ID", "feat-windows-port")
    _run(tasks_module.task_create, content="Get USB passthrough working")
    assert (tasks_module.TASKS_DIR / "feat-windows-port" / "tasks.json").exists()
    # The 'default' list stays empty.
    assert not (tasks_module.TASKS_DIR / "default" / "tasks.json").exists()


def test_list_id_defaults_to_default(tasks_module):
    _run(tasks_module.task_create, content="X")
    assert (tasks_module.TASKS_DIR / "default" / "tasks.json").exists()


# ── concurrency: flock on _lock_path ──────────────────────────


def test_concurrent_creates_do_not_lose_writes(tasks_module):
    """Race regression: 8 parallel asyncio task_create calls all
    succeed and produce 8 distinct ids. Without flock, two writers
    racing through _read_unlocked() + write would both compute the
    same next_id and the second overwrite wins, losing the first."""
    async def go():
        return await asyncio.gather(*(
            _unwrap(tasks_module.task_create)(content=f"Task {i}")
            for i in range(8)
        ))

    outs = asyncio.run(go())
    assert all("Task #" in o for o in outs), outs
    state = json.loads((tasks_module.TASKS_DIR / "default" / "tasks.json").read_text())
    ids = [t["id"] for t in state["tasks"]]
    assert sorted(ids, key=int) == [str(i) for i in range(1, 9)], (
        f"expected ids 1..8, got {ids}"
    )
    assert state["next_id"] == 9
