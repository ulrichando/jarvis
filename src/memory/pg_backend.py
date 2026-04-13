"""PostgreSQL backend for JARVIS short-term conversation memory.

Drop-in replacement for the SQLite conversation log in memory/store.py.
Falls back silently to SQLite if PostgreSQL is unavailable.

Configuration (env vars or ~/.jarvis/providers.json):
  JARVIS_PG_DSN      — full DSN e.g. postgresql://user:pass@host:5432/jarvis
  JARVIS_PG_HOST     — default: localhost
  JARVIS_PG_PORT     — default: 5432
  JARVIS_PG_DB       — default: jarvis
  JARVIS_PG_USER     — default: jarvis
  JARVIS_PG_PASSWORD — default: (empty)

Schema (auto-created on first connect):
  conversations(id, role, content, timestamp, session_id)
"""

import logging
import os
import time
import threading
from typing import Any

log = logging.getLogger("jarvis.memory.pg")

# ── DSN resolution ─────────────────────────────────────────────────────

def _build_dsn() -> str:
    dsn = os.environ.get("JARVIS_PG_DSN", "")
    if dsn:
        return dsn
    host = os.environ.get("JARVIS_PG_HOST", "localhost")
    port = os.environ.get("JARVIS_PG_PORT", "5432")
    db   = os.environ.get("JARVIS_PG_DB",   "jarvis")
    user = os.environ.get("JARVIS_PG_USER", "jarvis")
    pwd  = os.environ.get("JARVIS_PG_PASSWORD", "")
    if pwd:
        return f"postgresql://{user}:{pwd}@{host}:{port}/{db}"
    return f"postgresql://{user}@{host}:{port}/{db}"


# ── PostgreSQL backend ─────────────────────────────────────────────────

class PostgresConversationLog:
    """Conversation log backed by PostgreSQL.

    Thread-safe. Uses a connection pool (psycopg2 with a simple pool).
    All methods mirror the SQLite interface in MemoryStore so they are
    drop-in replaceable.
    """

    def __init__(self, dsn: str | None = None):
        self._dsn = dsn or _build_dsn()
        self._lock = threading.Lock()
        self._pool: list[Any] = []  # simple list-based connection pool
        self._pool_size = 4
        self._available = True

        try:
            self._init_pool()
            self._init_tables()
            log.info("PostgreSQL conversation log connected: %s", self._redact(self._dsn))
        except Exception as e:
            log.warning("PostgreSQL unavailable (%s) — will use SQLite fallback.", e)
            self._available = False

    @staticmethod
    def _redact(dsn: str) -> str:
        import re
        return re.sub(r"(:)[^:@]*(@)", r"\1***\2", dsn)

    def _init_pool(self):
        import psycopg2
        import psycopg2.extras
        self._psycopg2 = psycopg2
        # Create initial connections
        for _ in range(min(2, self._pool_size)):
            conn = psycopg2.connect(self._dsn)
            conn.autocommit = False
            self._pool.append(conn)

    def _get_conn(self):
        with self._lock:
            if self._pool:
                return self._pool.pop()
        # No free connection — create a new one
        conn = self._psycopg2.connect(self._dsn)
        conn.autocommit = False
        return conn

    def _release_conn(self, conn):
        try:
            conn.rollback()  # Reset any uncommitted state
        except Exception:
            pass
        with self._lock:
            if len(self._pool) < self._pool_size:
                self._pool.append(conn)
            else:
                try:
                    conn.close()
                except Exception:
                    pass

    def _init_tables(self):
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS conversations (
                        id         BIGSERIAL PRIMARY KEY,
                        role       TEXT        NOT NULL,
                        content    TEXT        NOT NULL,
                        timestamp  DOUBLE PRECISION NOT NULL,
                        session_id TEXT        DEFAULT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_conv_timestamp
                        ON conversations (timestamp);
                    CREATE INDEX IF NOT EXISTS idx_conv_session
                        ON conversations (session_id)
                        WHERE session_id IS NOT NULL;
                """)
                conn.commit()
        finally:
            self._release_conn(conn)

    # ── Public API (mirrors SQLite interface) ─────────────────────────

    def add_turn(self, role: str, content: str, session_id: str | None = None) -> None:
        if not self._available:
            return
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO conversations (role, content, timestamp, session_id) "
                    "VALUES (%s, %s, %s, %s)",
                    (role, content, time.time(), session_id),
                )
            conn.commit()
        except Exception as e:
            log.warning("PG add_turn error: %s", e)
            conn.rollback()
        finally:
            self._release_conn(conn)

    def get_history(
        self,
        limit: int = 20,
        time_cutoff: float = 0.0,
        effective_limit: int = 160,
    ) -> list[dict]:
        if not self._available:
            return []
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT role, content, timestamp FROM conversations "
                    "WHERE timestamp >= %s "
                    "ORDER BY timestamp DESC LIMIT %s",
                    (time_cutoff, effective_limit),
                )
                rows = cur.fetchall()
            return [
                {"role": r[0], "content": r[1], "timestamp": r[2]}
                for r in reversed(rows)
            ]
        except Exception as e:
            log.warning("PG get_history error: %s", e)
            return []
        finally:
            self._release_conn(conn)

    def list_sessions(self, gap_minutes: int = 30) -> list[dict]:
        if not self._available:
            return []
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, role, content, timestamp FROM conversations "
                    "ORDER BY timestamp ASC"
                )
                rows = cur.fetchall()
        except Exception as e:
            log.warning("PG list_sessions error: %s", e)
            return []
        finally:
            self._release_conn(conn)

        if not rows:
            return []

        gap = gap_minutes * 60
        sessions: list[list] = []
        current: list[dict] = []
        for row in rows:
            r = {"id": row[0], "role": row[1], "content": row[2], "timestamp": row[3]}
            if current and (r["timestamp"] - current[-1]["timestamp"]) > gap:
                sessions.append(current)
                current = []
            current.append(r)
        if current:
            sessions.append(current)

        result = []
        for turns in sessions:
            user_msgs = [t for t in turns if t["role"] == "user"]
            title = user_msgs[0]["content"][:60] if user_msgs else "..."
            result.append({
                "id": turns[0]["id"],
                "title": title,
                "start_ts": turns[0]["timestamp"],
                "end_ts": turns[-1]["timestamp"],
                "message_count": len(turns),
            })
        result.reverse()
        return result

    def delete_session(self, start_ts: float, end_ts: float) -> int:
        if not self._available:
            return 0
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM conversations WHERE timestamp >= %s AND timestamp <= %s",
                    (start_ts, end_ts),
                )
                count = cur.rowcount
            conn.commit()
            return count
        except Exception as e:
            log.warning("PG delete_session error: %s", e)
            conn.rollback()
            return 0
        finally:
            self._release_conn(conn)

    def close(self):
        with self._lock:
            for conn in self._pool:
                try:
                    conn.close()
                except Exception:
                    pass
            self._pool.clear()


# ── Factory ────────────────────────────────────────────────────────────

def get_pg_backend(dsn: str | None = None) -> PostgresConversationLog | None:
    """Return a PostgresConversationLog if PG is available, else None."""
    backend = PostgresConversationLog(dsn)
    return backend if backend._available else None
