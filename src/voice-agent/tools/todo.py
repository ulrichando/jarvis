"""Todo Tool — in-session task list for the JARVIS voice agent.

Registered tool name: ``todo``

Provides an in-session task list the supervisor uses to decompose complex
multi-step tasks, track progress, and maintain focus. State is held in a
module-level ``TodoStore`` instance (one per process / import chain) and
is re-injected into conversation context after context compression.

Design:
- Single ``todo`` tool: provide ``todos`` param to write, omit to read.
- Every call returns the full current list.
- No external dependencies — pure stdlib.

Ported from upstream; rewritten to be self-contained with zero platform
references. Handler receives a dict and returns a JSON string.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from .registry import registry, tool_error

logger = logging.getLogger(__name__)

# Valid status values for todo items
VALID_STATUSES = frozenset({"pending", "in_progress", "completed", "cancelled"})


# ---------------------------------------------------------------------------
# TodoStore
# ---------------------------------------------------------------------------

class TodoStore:
    """In-memory todo list. One instance per process (module-level singleton).

    Items are ordered — list position is priority. Each item:
      - id: unique string identifier (agent-chosen)
      - content: task description
      - status: pending | in_progress | completed | cancelled
    """

    def __init__(self) -> None:
        self._items: List[Dict[str, str]] = []

    def write(self, todos: List[Dict[str, Any]], merge: bool = False) -> List[Dict[str, str]]:
        """Write todos. Returns the full current list after writing.

        Args:
            todos: list of {id, content, status} dicts
            merge: if False, replace the entire list. If True, update
                   existing items by id and append new ones.
        """
        if not merge:
            self._items = [self._validate(t) for t in self._dedupe_by_id(todos)]
        else:
            existing = {item["id"]: item for item in self._items}
            for t in self._dedupe_by_id(todos):
                item_id = str(t.get("id", "")).strip()
                if not item_id:
                    continue
                if item_id in existing:
                    if "content" in t and t["content"]:
                        existing[item_id]["content"] = str(t["content"]).strip()
                    if "status" in t and t["status"]:
                        status = str(t["status"]).strip().lower()
                        if status in VALID_STATUSES:
                            existing[item_id]["status"] = status
                else:
                    validated = self._validate(t)
                    existing[validated["id"]] = validated
                    self._items.append(validated)
            seen: set = set()
            rebuilt: List[Dict[str, str]] = []
            for item in self._items:
                current = existing.get(item["id"], item)
                if current["id"] not in seen:
                    rebuilt.append(current)
                    seen.add(current["id"])
            self._items = rebuilt
        return self.read()

    def read(self) -> List[Dict[str, str]]:
        """Return a copy of the current list."""
        return [item.copy() for item in self._items]

    def has_items(self) -> bool:
        return bool(self._items)

    def format_for_injection(self) -> Optional[str]:
        """Render active items for context injection. Returns None when empty."""
        active = [
            item for item in self._items
            if item["status"] in {"pending", "in_progress"}
        ]
        if not active:
            return None
        markers = {
            "completed": "[x]", "in_progress": "[>]",
            "pending": "[ ]", "cancelled": "[~]",
        }
        lines = ["[Active task list preserved across context compression]"]
        for item in active:
            marker = markers.get(item["status"], "[?]")
            lines.append(f"- {marker} {item['id']}. {item['content']} ({item['status']})")
        return "\n".join(lines)

    @staticmethod
    def _validate(item: Dict[str, Any]) -> Dict[str, str]:
        item_id = str(item.get("id", "")).strip() or "?"
        content = str(item.get("content", "")).strip() or "(no description)"
        status = str(item.get("status", "pending")).strip().lower()
        if status not in VALID_STATUSES:
            status = "pending"
        return {"id": item_id, "content": content, "status": status}

    @staticmethod
    def _dedupe_by_id(todos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Collapse duplicate ids, keeping the last occurrence."""
        last_index: Dict[str, int] = {}
        for i, item in enumerate(todos):
            item_id = str(item.get("id", "")).strip() or "?"
            last_index[item_id] = i
        return [todos[i] for i in sorted(last_index.values())]


# Module-level singleton — one store per voice-agent process.
_store = TodoStore()


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

def _handle_todo(args: dict, **_kw) -> str:
    """Read or write the session todo list.

    Pass ``todos`` to write; omit it to read the current list.
    Always returns the full list with summary counts.
    """
    todos_arg = args.get("todos")
    merge = bool(args.get("merge", False))

    if todos_arg is not None:
        if not isinstance(todos_arg, list):
            return tool_error("todos must be an array of {id, content, status} objects")
        items = _store.write(todos_arg, merge=merge)
    else:
        items = _store.read()

    pending = sum(1 for i in items if i["status"] == "pending")
    in_progress = sum(1 for i in items if i["status"] == "in_progress")
    completed = sum(1 for i in items if i["status"] == "completed")
    cancelled = sum(1 for i in items if i["status"] == "cancelled")

    return json.dumps({
        "todos": items,
        "summary": {
            "total": len(items),
            "pending": pending,
            "in_progress": in_progress,
            "completed": completed,
            "cancelled": cancelled,
        },
    }, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Schema + registration
# ---------------------------------------------------------------------------

_TODO_SCHEMA = {
    "name": "todo",
    "description": (
        "Manage your task list for the current session. Use for complex tasks "
        "with 3 or more steps or when the user gives you multiple things to do. "
        "Call with no parameters to read the current list.\n\n"
        "Writing:\n"
        "  - Provide 'todos' array to create or update items.\n"
        "  - merge=false (default): replace the entire list with a fresh plan.\n"
        "  - merge=true: update existing items by id, add any new ones.\n\n"
        "Each item: {id: string, content: string, "
        "status: pending|in_progress|completed|cancelled}. "
        "List order is priority. Only ONE item in_progress at a time. "
        "Mark items completed immediately when done. If something fails, "
        "cancel it and add a revised item.\n\n"
        "Always returns the full current list."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "todos": {
                "type": "array",
                "description": "Task items to write. Omit to read the current list.",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {
                            "type": "string",
                            "description": "Unique item identifier (short, agent-chosen).",
                        },
                        "content": {
                            "type": "string",
                            "description": "Task description.",
                        },
                        "status": {
                            "type": "string",
                            "enum": ["pending", "in_progress", "completed", "cancelled"],
                            "description": "Current status.",
                        },
                    },
                    "required": ["id", "content", "status"],
                    "additionalProperties": False,
                },
            },
            "merge": {
                "type": "boolean",
                "description": (
                    "true: update existing items by id, add new ones. "
                    "false (default): replace the entire list."
                ),
            },
        },
        "required": [],
    },
}


registry.register(
    name="todo",
    schema=_TODO_SCHEMA,
    handler=_handle_todo,
    toolset="builtin",
    description=(
        "Session task list — read or write a structured todo list to track "
        "multi-step work. Omit todos param to read; pass todos array to write."
    ),
    emoji="📋",
)
