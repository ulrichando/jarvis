"""Telemetry — tracks JARVIS usage for self-evolution.

Logs every interaction so the evolution engine can analyze patterns
and generate improvements.
"""

import sqlite3
import time
from src.config import DATA_DIR


class Telemetry:
    """Collects usage data for the self-evolution engine."""

    def __init__(self, db_path: str | None = None):
        if db_path is None:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            db_path = str(DATA_DIR / "telemetry.db")
        self.conn = sqlite3.connect(db_path, timeout=30, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.row_factory = sqlite3.Row
        self._init_tables()

    def _init_tables(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS interactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                user_input TEXT NOT NULL,
                intent TEXT,
                response_text TEXT,
                response_latency_ms REAL,
                tokens_used INTEGER,
                model_used TEXT,
                success INTEGER DEFAULT 1,
                error TEXT
            );

            CREATE TABLE IF NOT EXISTS commands (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                command TEXT NOT NULL,
                exit_code INTEGER,
                success INTEGER DEFAULT 1
            );

            CREATE INDEX IF NOT EXISTS idx_interactions_ts
                ON interactions(timestamp);
        """)
        self.conn.commit()

    def log_interaction(
        self,
        user_input: str,
        response_text: str,
        intent: str = "",
        latency_ms: float = 0,
        tokens_used: int = 0,
        model_used: str = "",
        success: bool = True,
        error: str = "",
    ):
        """Log a user interaction."""
        self.conn.execute(
            "INSERT INTO interactions "
            "(timestamp, user_input, intent, response_text, response_latency_ms, "
            "tokens_used, model_used, success, error) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (time.time(), user_input, intent, response_text, latency_ms,
             tokens_used, model_used, int(success), error),
        )
        self.conn.commit()

    def log_command(self, command: str, exit_code: int, success: bool):
        """Log a command execution."""
        self.conn.execute(
            "INSERT INTO commands (timestamp, command, exit_code, success) "
            "VALUES (?, ?, ?, ?)",
            (time.time(), command, exit_code, int(success)),
        )
        self.conn.commit()

    def get_common_queries(self, days: int = 7, limit: int = 20) -> list[dict]:
        """Get the most common user queries in the last N days."""
        since = time.time() - (days * 86400)
        rows = self.conn.execute(
            "SELECT user_input, COUNT(*) as count "
            "FROM interactions WHERE timestamp > ? "
            "GROUP BY user_input ORDER BY count DESC LIMIT ?",
            (since, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_failed_interactions(self, days: int = 7) -> list[dict]:
        """Get failed interactions for debugging."""
        since = time.time() - (days * 86400)
        rows = self.conn.execute(
            "SELECT * FROM interactions "
            "WHERE timestamp > ? AND success = 0 "
            "ORDER BY timestamp DESC",
            (since,),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_avg_latency(self, days: int = 7) -> float:
        """Get average response latency in ms."""
        since = time.time() - (days * 86400)
        row = self.conn.execute(
            "SELECT AVG(response_latency_ms) FROM interactions "
            "WHERE timestamp > ? AND response_latency_ms > 0",
            (since,),
        ).fetchone()
        return row[0] or 0.0

    def close(self):
        self.conn.close()
