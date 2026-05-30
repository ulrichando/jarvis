from __future__ import annotations
import sqlite3
from pathlib import Path
from typing import Optional

DEFAULT_TELEMETRY_DB = Path.home() / ".local/share/jarvis/turn_telemetry.db"
_COLS = ["ts_utc", "route", "ttfw_ms", "interrupted", "confab_check_state", "user_text"]

def read_turns(db_path: Path = DEFAULT_TELEMETRY_DB, *, since: Optional[str] = None,
               until: Optional[str] = None, limit: Optional[int] = None) -> list[dict]:
    """Read turns read-only as list[dict]. Returns [] if the file/table is absent or has none
    of the needed columns. Missing columns come back as None."""
    if not Path(db_path).exists(): return []
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True); con.row_factory = sqlite3.Row
    tables = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "turns" not in tables: con.close(); return []
    have = {r[1] for r in con.execute("PRAGMA table_info(turns)").fetchall()}
    sel = [c for c in _COLS if c in have]
    if not sel: con.close(); return []
    where, args = [], []
    if since and "ts_utc" in have: where.append("ts_utc >= ?"); args.append(since)
    if until and "ts_utc" in have: where.append("ts_utc <= ?"); args.append(until)
    sql = f"SELECT {','.join(sel)} FROM turns"
    if where: sql += " WHERE " + " AND ".join(where)
    if "ts_utc" in have: sql += " ORDER BY ts_utc ASC"
    if limit: sql += f" LIMIT {int(limit)}"
    rows = [dict(r) for r in con.execute(sql, args).fetchall()]; con.close()
    for r in rows:
        for c in _COLS: r.setdefault(c, None)
    return rows
