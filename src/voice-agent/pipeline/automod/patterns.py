"""Pattern detector for the auto-mod loop (Spec B, Plane 3).

scans turn_telemetry.db for 3 pattern classes:
  - correction repeat (≥3 same signal in `turns.correction_signal`)
  - confab self-flag (≥3 turns with confab_check_state='save_claim'
    in last CONFAB_WINDOW_DAYS days)
  - tool-gap repeat (deferred — table exists but bucketing logic is
    a future iteration)

On threshold + not already proposed, emits a record to
~/.jarvis/auto-mods/queue.jsonl and stamps proposed_at in the
corresponding tracking table.

Pure read on `turns`; writes only to (a) the new tracking tables (to set
proposed_at) and (b) queue.jsonl. Never raises — returns 0 emitted on any
DB error. Default cadence is set by the caller (jarvis_agent.py
schedules this); this module just provides scan_and_emit().
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import time
from pathlib import Path

from pipeline.automod._state import queue_path

logger = logging.getLogger("jarvis.automod.patterns")

THRESHOLD = 3
CONFAB_WINDOW_DAYS = 7


def _telemetry_db_path() -> Path:
    p = os.environ.get("JARVIS_TURN_TELEMETRY_DB")
    if p:
        return Path(p)
    home = os.environ.get("JARVIS_HOME") or str(Path.home() / ".local/share/jarvis")
    return Path(home) / "turn_telemetry.db"


def _ensure_queue_dir() -> None:
    queue_path().parent.mkdir(parents=True, exist_ok=True)


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _emit(record: dict) -> None:
    _ensure_queue_dir()
    line = json.dumps(record, ensure_ascii=False)
    with queue_path().open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    logger.info("[automod] pattern detected: kind=%s id=%s",
                record["kind"], record["id"])


def _next_id(kind: str) -> str:
    suffix = hashlib.sha1(
        f"{kind}-{time.time_ns()}".encode()
    ).hexdigest()[:6]
    return f"automod-{time.strftime('%Y-%m-%d', time.gmtime())}-{suffix}"


def _scan_corrections(conn: sqlite3.Connection) -> int:
    emitted = 0
    rows = conn.execute("""
        SELECT correction_signal, COUNT(*) AS c,
               MIN(ts_utc) AS first_seen, MAX(ts_utc) AS last_seen
          FROM turns
         WHERE correction_signal IS NOT NULL AND correction_signal != ''
      GROUP BY correction_signal
        HAVING c >= ?
    """, (THRESHOLD,)).fetchall()
    for signal, count, first_seen, last_seen in rows:
        existing = conn.execute(
            "SELECT proposed_at FROM recurring_corrections WHERE signal=?",
            (signal,),
        ).fetchone()
        if existing and existing[0]:
            continue
        if existing:
            conn.execute(
                "UPDATE recurring_corrections SET last_seen=?, count=? WHERE signal=?",
                (last_seen, count, signal),
            )
        else:
            conn.execute(
                """INSERT INTO recurring_corrections
                   (signal, first_seen, last_seen, count) VALUES (?, ?, ?, ?)""",
                (signal, first_seen, last_seen, count),
            )
        rec_id = _next_id("correction")
        _emit({
            "id": rec_id,
            "kind": "correction",
            "intent": f"Investigate the recurring correction: {signal!r}. "
                      f"Find the file (likely a prompt under prompts/) where "
                      f"the offending behavior originates and patch it.",
            "rationale": f"user corrected this {count} times "
                         f"({first_seen} → {last_seen})",
            "evidence": {"signal": signal, "count": count,
                         "first_seen": first_seen, "last_seen": last_seen},
            "created_at": _now_iso(),
        })
        conn.execute(
            "UPDATE recurring_corrections SET proposed_at=? WHERE signal=?",
            (_now_iso(), signal),
        )
        emitted += 1
    conn.commit()
    return emitted


def _scan_confabs(conn: sqlite3.Connection) -> int:
    cutoff = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ",
        time.gmtime(time.time() - CONFAB_WINDOW_DAYS * 86400),
    )
    row = conn.execute(
        """SELECT COUNT(*), MAX(ts_utc) FROM turns
            WHERE confab_check_state = 'save_claim' AND ts_utc >= ?""",
        (cutoff,),
    ).fetchone()
    count, last_seen = row[0] or 0, row[1]
    if count < THRESHOLD:
        return 0
    signal = f"__confab_save_claim_window_{CONFAB_WINDOW_DAYS}d__"
    existing = conn.execute(
        "SELECT proposed_at FROM recurring_corrections WHERE signal=?",
        (signal,),
    ).fetchone()
    if existing and existing[0]:
        return 0
    rec_id = _next_id("confab")
    _emit({
        "id": rec_id,
        "kind": "confab",
        "intent": "Investigate the recurring save-claim confabulation pattern. "
                  "JARVIS is saying 'I'll remember' / 'saved' without actually "
                  "calling memory(). Likely a prompt-strength issue in the "
                  "supervisor or memory tool description.",
        "rationale": f"{count} save_claim confabs in last {CONFAB_WINDOW_DAYS} days",
        "evidence": {"count": count, "last_seen": last_seen,
                     "window_days": CONFAB_WINDOW_DAYS},
        "created_at": _now_iso(),
    })
    if existing:
        conn.execute(
            "UPDATE recurring_corrections SET proposed_at=?, count=?, last_seen=? WHERE signal=?",
            (_now_iso(), count, last_seen, signal),
        )
    else:
        conn.execute(
            """INSERT INTO recurring_corrections
               (signal, first_seen, last_seen, count, proposed_at)
               VALUES (?, ?, ?, ?, ?)""",
            (signal, last_seen, last_seen, count, _now_iso()),
        )
    conn.commit()
    return 1


def scan_and_emit() -> int:
    """Scan all pattern classes; emit intents that crossed threshold.
    Returns total intents emitted across all classes."""
    db = _telemetry_db_path()
    if not db.exists():
        logger.debug("[automod] telemetry db missing: %s", db)
        return 0
    emitted = 0
    try:
        with sqlite3.connect(str(db)) as conn:
            emitted += _scan_corrections(conn)
            emitted += _scan_confabs(conn)
    except sqlite3.Error as e:
        logger.warning("[automod] scan failed: %s: %s",
                       type(e).__name__, e)
        return 0
    if emitted:
        logger.info("[automod] emitted %d intent(s) this scan", emitted)
    return emitted
