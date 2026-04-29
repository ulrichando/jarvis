"""SQLite turn telemetry. Non-blocking writes; failures are silent.

Every JARVIS turn writes one row. Reading is via `--report`.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Optional

DEFAULT_DB_PATH = Path(
    os.environ.get(
        "JARVIS_TELEMETRY_PATH",
        Path.home() / ".local" / "share" / "jarvis" / "turn_telemetry.db",
    )
).expanduser()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS turns (
    id INTEGER PRIMARY KEY,
    ts_utc TEXT NOT NULL,
    user_text TEXT NOT NULL,
    jarvis_text TEXT NOT NULL,
    emotion TEXT,
    route TEXT,
    llm_used TEXT,
    voice_used TEXT,
    ttfw_ms INTEGER,
    total_audio_ms INTEGER,
    user_followup_30s INTEGER,
    route_fallback INTEGER,
    notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_turns_ts_utc ON turns(ts_utc);
CREATE INDEX IF NOT EXISTS idx_turns_route   ON turns(route);
"""


def init_db(db_path: Path = DEFAULT_DB_PATH) -> None:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(_SCHEMA)


def log_turn(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    user_text: str,
    jarvis_text: str,
    emotion: Optional[str],
    route: Optional[str],
    llm_used: Optional[str],
    voice_used: Optional[str],
    ttfw_ms: Optional[int],
    total_audio_ms: Optional[int],
    user_followup_30s: bool,
    route_fallback: bool,
    notes: str = "",
) -> None:
    """Write one row. Any exception is swallowed so telemetry never blocks voice."""
    try:
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """INSERT INTO turns
                   (ts_utc, user_text, jarvis_text, emotion, route, llm_used,
                    voice_used, ttfw_ms, total_audio_ms, user_followup_30s,
                    route_fallback, notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    user_text, jarvis_text, emotion, route, llm_used,
                    voice_used, ttfw_ms, total_audio_ms,
                    int(user_followup_30s), int(route_fallback), notes,
                ),
            )
    except Exception:
        return  # silent — see module docstring


def report(db_path: Path = DEFAULT_DB_PATH) -> str:
    """Print a human-readable summary of telemetry."""
    if not Path(db_path).exists():
        return "no telemetry yet"
    out: list[str] = []
    with sqlite3.connect(db_path) as conn:
        n = conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
        out.append(f"total turns: {n}")
        for route, count, avg_ttfw, max_ttfw in conn.execute(
            """SELECT route, COUNT(*),
                      CAST(AVG(ttfw_ms) AS INT),
                      MAX(ttfw_ms)
               FROM turns GROUP BY route ORDER BY count DESC"""
        ):
            out.append(f"  {route or '?'}: {count} turns, avg_ttfw={avg_ttfw}ms, max_ttfw={max_ttfw}ms")
        emo_followup = conn.execute(
            "SELECT AVG(user_followup_30s) FROM turns WHERE route='EMOTIONAL'"
        ).fetchone()[0]
        out.append(f"emotional follow-up rate: {emo_followup or 0:.0%}")
        fb = conn.execute("SELECT AVG(route_fallback) FROM turns").fetchone()[0] or 0
        out.append(f"route-fallback rate: {fb:.1%}")
    return "\n".join(out)


if __name__ == "__main__":
    if "--report" in sys.argv:
        print(report())
    else:
        init_db()
        print(f"initialized {DEFAULT_DB_PATH}")
