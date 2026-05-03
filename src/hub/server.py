"""JARVIS event hub daemon.

Reads `events:*` Redis Streams via consumer groups, applies events
idempotently to ~/.jarvis/hub/state.db, re-publishes normalized
events to `broadcasts:*` streams.

This file currently only contains the schema bootstrap. Consumer
loop and main daemon entry point come in Tasks 3-4.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

# Resolved at import time so tests can patch.
SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def bootstrap_schema(db_path: Path | str) -> None:
    """Apply schema.sql to the state DB. Idempotent."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    sql = SCHEMA_PATH.read_text()
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(sql)
        conn.commit()
    finally:
        conn.close()
