"""JARVIS Session Manager — persistent, resumable conversations.

Inspired by Claude Code's session system:
- Sessions are auto-saved with unique IDs
- Resume with name or ID (jarvis-cli -c to continue last, -r <name> to resume)
- Each session stores full message history + metadata
- Sessions are stored in SQLite for fast access
"""

import sqlite3
import json
import re
import time
import uuid
from brain.config import DATA_DIR

# Session IDs must be alphanumeric (plus hyphens/underscores), max 64 chars.
# This prevents path traversal and injection via crafted session IDs.
_VALID_SESSION_ID = re.compile(r'^[a-zA-Z0-9_-]{1,64}$')


def _validate_session_id(session_id: str) -> str:
    """Validate a session ID against the allowed pattern.

    Raises ValueError if the ID contains disallowed characters or is too long.
    Returns the session_id unchanged if valid.
    """
    if not _VALID_SESSION_ID.match(session_id):
        raise ValueError(
            f"Invalid session ID {session_id!r}: must be 1-64 alphanumeric, "
            "hyphen, or underscore characters"
        )
    return session_id


class Session:
    """A single JARVIS conversation session."""

    __slots__ = ("id", "name", "created_at", "updated_at", "mode", "messages", "metadata")

    def __init__(
        self,
        session_id: str | None = None,
        name: str | None = None,
        mode: str = "normal",
    ):
        self.id = _validate_session_id(session_id) if session_id else uuid.uuid4().hex[:12]
        self.name = name or ""
        self.created_at = time.time()
        self.updated_at = time.time()
        self.mode = mode
        self.messages: list[dict] = []
        self.metadata: dict = {}

    @property
    def display_name(self) -> str:
        if self.name:
            return self.name
        # Auto-generate from first user message
        for msg in self.messages:
            if msg.get("role") == "user":
                text = msg["content"][:50]
                return text + ("..." if len(msg["content"]) > 50 else "")
        return self.id[:8]

    @property
    def turn_count(self) -> int:
        return len([m for m in self.messages if m["role"] == "user"])

    def add_message(self, role: str, content: str):
        self.messages.append({
            "role": role,
            "content": content,
            "timestamp": time.time(),
        })
        self.updated_at = time.time()


class SessionManager:
    """Manages persistent sessions in SQLite."""

    def __init__(self, db_path: str | None = None):
        if db_path is None:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            db_path = str(DATA_DIR / "sessions.db")
        self.conn = sqlite3.connect(db_path, timeout=10, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.row_factory = sqlite3.Row
        self._init_tables()
        self._current: Session | None = None

    def _init_tables(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                name TEXT DEFAULT '',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                mode TEXT DEFAULT 'normal',
                messages TEXT DEFAULT '[]',
                metadata TEXT DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_sessions_updated
                ON sessions(updated_at DESC);
        """)
        self.conn.commit()

    # ── Session CRUD ────────────────────────────────────────────────

    def new(self, name: str = "", mode: str = "normal") -> Session:
        """Create a new session and set it as current."""
        session = Session(name=name, mode=mode)
        self._save(session)
        self._current = session
        return session

    def _save(self, session: Session):
        """Persist a session to disk."""
        self.conn.execute(
            """INSERT OR REPLACE INTO sessions
               (id, name, created_at, updated_at, mode, messages, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                session.id,
                session.name,
                session.created_at,
                session.updated_at,
                session.mode,
                json.dumps(session.messages),
                json.dumps(session.metadata),
            ),
        )
        self.conn.commit()

    def save_current(self):
        """Save the current session."""
        if self._current:
            self._save(self._current)

    def get(self, session_id: str) -> Session | None:
        """Load a session by ID."""
        _validate_session_id(session_id)
        # NOTE: parameterized queries already prevent SQL injection here.
        row = self.conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if not row:
            return None
        return self._row_to_session(row)

    def get_latest(self) -> Session | None:
        """Get the most recently updated session."""
        row = self.conn.execute(
            "SELECT * FROM sessions ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
        if not row:
            return None
        return self._row_to_session(row)

    def find(self, query: str) -> Session | None:
        """Find a session by name or ID prefix.

        The query is validated as a session ID before any ID-based lookup.
        Name-based lookups use parameterized queries (safe from injection).
        """
        _validate_session_id(query)
        # Try exact ID match
        session = self.get(query)
        if session:
            return session
        # Try name match
        row = self.conn.execute(
            "SELECT * FROM sessions WHERE name = ? ORDER BY updated_at DESC LIMIT 1",
            (query,),
        ).fetchone()
        if row:
            return self._row_to_session(row)
        # Try ID prefix
        row = self.conn.execute(
            "SELECT * FROM sessions WHERE id LIKE ? ORDER BY updated_at DESC LIMIT 1",
            (f"{query}%",),
        ).fetchone()
        if row:
            return self._row_to_session(row)
        # Try name substring
        row = self.conn.execute(
            "SELECT * FROM sessions WHERE name LIKE ? ORDER BY updated_at DESC LIMIT 1",
            (f"%{query}%",),
        ).fetchone()
        if row:
            return self._row_to_session(row)
        return None

    def list_sessions(self, limit: int = 20) -> list[dict]:
        """List recent sessions."""
        rows = self.conn.execute(
            "SELECT id, name, created_at, updated_at, mode, messages FROM sessions "
            "ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        result = []
        for row in rows:
            msgs = json.loads(row["messages"])
            user_turns = len([m for m in msgs if m.get("role") == "user"])
            first_msg = ""
            for m in msgs:
                if m.get("role") == "user":
                    first_msg = m["content"][:60]
                    break
            result.append({
                "id": row["id"],
                "name": row["name"],
                "turns": user_turns,
                "updated": row["updated_at"],
                "mode": row["mode"],
                "preview": first_msg,
            })
        return result

    def delete(self, session_id: str) -> bool:
        """Delete a session."""
        self.conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        self.conn.commit()
        return self.conn.total_changes > 0

    # ── Current session ─────────────────────────────────────────────

    @property
    def current(self) -> Session | None:
        return self._current

    def resume(self, session: Session):
        """Set a loaded session as current."""
        self._current = session

    def add_message(self, role: str, content: str):
        """Add a message to the current session and auto-save."""
        if self._current:
            self._current.add_message(role, content)
            # Auto-save every few messages
            if len(self._current.messages) % 4 == 0:
                self._save(self._current)

    # ── Internal ────────────────────────────────────────────────────

    def _row_to_session(self, row) -> Session:
        session = Session(session_id=row["id"], name=row["name"], mode=row["mode"])
        session.created_at = row["created_at"]
        session.updated_at = row["updated_at"]
        session.messages = json.loads(row["messages"])
        session.metadata = json.loads(row["metadata"] if "metadata" in row.keys() else "{}")
        return session

    def close(self):
        """Save and close."""
        self.save_current()
        self.conn.close()
