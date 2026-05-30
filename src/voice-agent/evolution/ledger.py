from __future__ import annotations
import json, sqlite3
from pathlib import Path
from typing import Optional

DEFAULT_LEDGER_DB = Path.home() / ".local/share/jarvis/evolution_ledger.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc TEXT NOT NULL, window_start TEXT, window_end TEXT,
    n_turns INTEGER NOT NULL, per_axis_json TEXT NOT NULL, composite REAL NOT NULL,
    guardrail_json TEXT NOT NULL, passed INTEGER NOT NULL, candidate_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_readings_ts ON readings(ts_utc);
"""

def init_ledger(db_path: Path = DEFAULT_LEDGER_DB) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path)); con.executescript(_SCHEMA); con.commit(); con.close()

def append_reading(*, ts_utc: str, window_start: Optional[str], window_end: Optional[str],
                   n_turns: int, per_axis: dict, composite: float, guardrail_flags: dict,
                   passed: bool, candidate_id: Optional[str] = None,
                   db_path: Path = DEFAULT_LEDGER_DB) -> int:
    """Append one fitness reading. Returns new row id. Append-only: no update/delete API."""
    init_ledger(db_path)
    con = sqlite3.connect(str(db_path))
    cur = con.execute(
        "INSERT INTO readings(ts_utc,window_start,window_end,n_turns,per_axis_json,composite,"
        "guardrail_json,passed,candidate_id) VALUES (?,?,?,?,?,?,?,?,?)",
        (ts_utc, window_start, window_end, n_turns, json.dumps(per_axis), composite,
         json.dumps(guardrail_flags), 1 if passed else 0, candidate_id))
    con.commit(); rid = cur.lastrowid; con.close(); return rid

def read_readings(limit: int = 50, db_path: Path = DEFAULT_LEDGER_DB) -> list[dict]:
    if not Path(db_path).exists(): return []
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True); con.row_factory = sqlite3.Row
    rows = con.execute("SELECT * FROM readings ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    con.close()
    out = []
    for r in rows:
        d = dict(r); d["per_axis"] = json.loads(d.pop("per_axis_json"))
        d["guardrail_flags"] = json.loads(d.pop("guardrail_json"))
        d["passed"] = bool(d["passed"])          # coerce 1/0 -> bool (clean contract)
        out.append(d)
    return out
