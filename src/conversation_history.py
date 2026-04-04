"""JARVIS Conversation History — SQLite-backed conversation log."""

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class ConversationHistory:
    """Append-only conversation log with session tracking."""

    def __init__(self, db_dir: str):
        self.db_path = Path(db_dir) / "conversations.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.session_id = str(uuid.uuid4())
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS turns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                metadata TEXT DEFAULT '{}',
                turn_index INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        self._conn.commit()
        self._turn_counter = 0

    def add_turn(self, role: str, content: str, metadata: dict[str, Any] | None = None) -> None:
        self._conn.execute(
            "INSERT INTO turns (session_id, role, content, metadata, turn_index, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (self.session_id, role, content, json.dumps(metadata or {}), self._turn_counter,
             datetime.now(timezone.utc).isoformat()),
        )
        self._conn.commit()
        self._turn_counter += 1

    def get_recent(self, n: int) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM turns WHERE session_id = ? ORDER BY turn_index DESC LIMIT ?",
            (self.session_id, n),
        ).fetchall()
        return [self._row_to_dict(r) for r in reversed(rows)]

    def search(self, query: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM turns WHERE content LIKE ? ORDER BY turn_index",
            (f"%{query}%",),
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def get_session(self, session_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM turns WHERE session_id = ? ORDER BY turn_index",
            (session_id,),
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def list_sessions(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT session_id, COUNT(*) as turn_count, MIN(created_at) as started FROM turns GROUP BY session_id ORDER BY started DESC"
        ).fetchall()
        return [{"session_id": r["session_id"], "turn_count": r["turn_count"], "started": r["started"]} for r in rows]

    def export(self, fmt: str = "json") -> str:
        turns = self.get_recent(10000)
        if fmt == "json":
            return json.dumps(turns, indent=2)
        lines = []
        for t in turns:
            name = "JARVIS" if t["role"] == "assistant" else "User"
            lines.append(f"**{name}**: {t['content']}")
        return "\n\n".join(lines)

    def clear_session(self, session_id: str) -> None:
        self._conn.execute("DELETE FROM turns WHERE session_id = ?", (session_id,))
        self._conn.commit()

    def _row_to_dict(self, row: sqlite3.Row) -> dict:
        d = dict(row)
        d["metadata"] = json.loads(d.get("metadata", "{}"))
        return d
