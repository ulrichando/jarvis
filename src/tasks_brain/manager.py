"""TaskManager — SQLite-backed task tracking."""

import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src.config import DATA_DIR, ensure_dirs

VALID_STATUSES = {"pending", "in_progress", "done", "failed"}
VALID_PRIORITIES = {"low", "medium", "high", "critical"}


@dataclass
class Task:
    id: str
    title: str
    status: str = "pending"
    priority: str = "medium"
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    description: str = ""
    tags: str = ""  # comma-separated

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "status": self.status,
            "priority": self.priority,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "description": self.description,
            "tags": self.tags,
        }


class TaskManager:
    """Persistent task management backed by SQLite."""

    def __init__(self, db_path: Optional[Path] = None):
        ensure_dirs()
        self.db_path = db_path or (DATA_DIR / "tasks.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    # ── Database setup ────────────────────────────────────────────────

    def _init_db(self):
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id          TEXT PRIMARY KEY,
                    title       TEXT NOT NULL,
                    status      TEXT NOT NULL DEFAULT 'pending',
                    priority    TEXT NOT NULL DEFAULT 'medium',
                    created_at  TEXT NOT NULL,
                    updated_at  TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    tags        TEXT DEFAULT ''
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)
            """)
            # Dependency graph: blocker_id must complete before blocked_id can start
            conn.execute("""
                CREATE TABLE IF NOT EXISTS task_dependencies (
                    blocker_id  TEXT NOT NULL,
                    blocked_id  TEXT NOT NULL,
                    PRIMARY KEY (blocker_id, blocked_id),
                    FOREIGN KEY (blocker_id) REFERENCES tasks(id) ON DELETE CASCADE,
                    FOREIGN KEY (blocked_id) REFERENCES tasks(id) ON DELETE CASCADE
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_dep_blocker ON task_dependencies(blocker_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_dep_blocked ON task_dependencies(blocked_id)
            """)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    # ── CRUD ──────────────────────────────────────────────────────────

    def create(self, title: str, priority: str = "medium",
               description: str = "", tags: str = "") -> Task:
        """Create a new task and persist it."""
        if priority not in VALID_PRIORITIES:
            raise ValueError(f"Invalid priority {priority!r}. Choose from {VALID_PRIORITIES}")

        now = datetime.now(timezone.utc).isoformat()
        task = Task(
            id=uuid.uuid4().hex[:12],
            title=title,
            status="pending",
            priority=priority,
            created_at=now,
            updated_at=now,
            description=description,
            tags=tags,
        )
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO tasks (id, title, status, priority, created_at, updated_at, description, tags) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (task.id, task.title, task.status, task.priority,
                 task.created_at, task.updated_at, task.description, task.tags),
            )
        return task

    def get(self, task_id: str) -> Optional[Task]:
        """Get a task by ID."""
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            return None
        return self._row_to_task(row)

    def update_status(self, task_id: str, status: str) -> Optional[Task]:
        """Update a task's status."""
        if status not in VALID_STATUSES:
            raise ValueError(f"Invalid status {status!r}. Choose from {VALID_STATUSES}")

        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?",
                (status, now, task_id),
            )
            if cur.rowcount == 0:
                return None
        return self.get(task_id)

    def update(self, task_id: str, **fields) -> Optional[Task]:
        """Update arbitrary fields on a task."""
        allowed = {"title", "status", "priority", "description", "tags"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return self.get(task_id)
        if "status" in updates and updates["status"] not in VALID_STATUSES:
            raise ValueError(f"Invalid status {updates['status']!r}")
        if "priority" in updates and updates["priority"] not in VALID_PRIORITIES:
            raise ValueError(f"Invalid priority {updates['priority']!r}")

        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [task_id]

        with self._conn() as conn:
            cur = conn.execute(
                f"UPDATE tasks SET {set_clause} WHERE id = ?", values,
            )
            if cur.rowcount == 0:
                return None
        return self.get(task_id)

    def delete(self, task_id: str) -> bool:
        """Delete a task. Returns True if it existed."""
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        return cur.rowcount > 0

    # ── Queries ───────────────────────────────────────────────────────

    def list_tasks(self, status_filter: Optional[str] = None,
                   priority_filter: Optional[str] = None,
                   limit: int = 100) -> list[Task]:
        """List tasks, optionally filtered by status and/or priority."""
        query = "SELECT * FROM tasks"
        params: list = []
        clauses: list[str] = []

        if status_filter:
            clauses.append("status = ?")
            params.append(status_filter)
        if priority_filter:
            clauses.append("priority = ?")
            params.append(priority_filter)

        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY CASE priority WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END, created_at DESC"
        query += f" LIMIT {limit}"

        with self._conn() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._row_to_task(r) for r in rows]

    def count(self, status_filter: Optional[str] = None) -> int:
        """Count tasks, optionally by status."""
        if status_filter:
            with self._conn() as conn:
                row = conn.execute("SELECT COUNT(*) FROM tasks WHERE status = ?", (status_filter,)).fetchone()
        else:
            with self._conn() as conn:
                row = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()
        return row[0]

    # ── Dependency Graph ─────────────────────────────────────────────

    def add_dependency(self, blocker_id: str, blocked_id: str) -> bool:
        """Declare that *blocker_id* must complete before *blocked_id* can start.

        Returns False if either task doesn't exist.
        """
        if not self.get(blocker_id) or not self.get(blocked_id):
            return False
        with self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO task_dependencies (blocker_id, blocked_id) VALUES (?, ?)",
                (blocker_id, blocked_id),
            )
        return True

    def remove_dependency(self, blocker_id: str, blocked_id: str) -> bool:
        """Remove a dependency edge. Returns True if it existed."""
        with self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM task_dependencies WHERE blocker_id = ? AND blocked_id = ?",
                (blocker_id, blocked_id),
            )
        return cur.rowcount > 0

    def get_blockers(self, task_id: str) -> list[Task]:
        """Return all tasks that must complete before *task_id* can start."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT t.* FROM tasks t "
                "JOIN task_dependencies d ON d.blocker_id = t.id "
                "WHERE d.blocked_id = ?",
                (task_id,),
            ).fetchall()
        return [self._row_to_task(r) for r in rows]

    def get_blocked_tasks(self, task_id: str) -> list[Task]:
        """Return all tasks that are blocked by *task_id*."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT t.* FROM tasks t "
                "JOIN task_dependencies d ON d.blocked_id = t.id "
                "WHERE d.blocker_id = ?",
                (task_id,),
            ).fetchall()
        return [self._row_to_task(r) for r in rows]

    def is_blocked(self, task_id: str) -> bool:
        """Return True if *task_id* has any incomplete blocker tasks."""
        blockers = self.get_blockers(task_id)
        return any(b.status != "done" for b in blockers)

    def get_ready_tasks(self, priority_filter: Optional[str] = None) -> list[Task]:
        """Return pending tasks that have no incomplete blockers (ready to start).

        Excludes tasks already in_progress or done.
        """
        pending = self.list_tasks(status_filter="pending", priority_filter=priority_filter)
        return [t for t in pending if not self.is_blocked(t.id)]

    def can_start(self, task_id: str) -> tuple[bool, list[Task]]:
        """Check if a task can be started.

        Returns (can_start, list_of_incomplete_blockers).
        """
        blockers = self.get_blockers(task_id)
        incomplete = [b for b in blockers if b.status != "done"]
        return len(incomplete) == 0, incomplete

    # ── Internal ──────────────────────────────────────────────────────

    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> Task:
        return Task(
            id=row["id"],
            title=row["title"],
            status=row["status"],
            priority=row["priority"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            description=row["description"] or "",
            tags=row["tags"] or "",
        )
