"""Fallback population of recurring_errors from voice-agent.log.

Used when the ErrorTelemetryHandler hasn't been wired yet (fresh
session, table is empty) but the log file already has 24h of
exception records we shouldn't ignore. Best-effort; failures
return 0 silently.

Spec 2026-05-27 Part 4."""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path

from pipeline.automod.error_logger import (
    _is_jarvis_owned,
    _signature,
    _fixability_score,
    _upsert,
    _ignore_set,
    _truncate_tb,
)

logger = logging.getLogger("jarvis.automod.error_log_fallback")

LOG_PATH = Path.home() / ".local/share/jarvis/logs/voice-agent.log"
LOOKBACK_SECONDS = 24 * 3600

_FRAME_RE = re.compile(
    r'File "([^"]+)", line (\d+), in (\w+)',
)


def populate_from_log_if_empty(conn: sqlite3.Connection) -> int:
    """If recurring_errors is empty, seed it from the last 24h of
    voice-agent.log. Returns count of new records ingested (each ingest
    may upsert into the same signature, so this counts events not unique
    signatures).

    Always-safe: returns 0 on any failure."""
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM recurring_errors"
        ).fetchone()
        if row[0] > 0:
            return 0
    except sqlite3.Error:
        return 0  # table missing or other — abort cleanly

    if not LOG_PATH.exists():
        return 0

    cutoff_ts = time.time() - LOOKBACK_SECONDS
    ingested = 0
    try:
        with LOG_PATH.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                rec = _parse_log_line(line)
                if rec is None:
                    continue
                if rec["ts"] < cutoff_ts:
                    continue
                if rec["level"] != "ERROR":
                    continue
                exc_class = rec.get("exc_class")
                if not exc_class or exc_class in _ignore_set():
                    continue
                frames = _frames_from_tb(rec.get("traceback", ""))
                if not frames:
                    continue
                sig = _signature(exc_class, frames)
                sample_tb = _truncate_tb(rec.get("traceback", ""))
                exc_message = rec.get("exc_message", "")
                fixability = _fixability_score(exc_class, exc_message, frames)
                _upsert(conn, sig, exc_class, exc_message, frames,
                        sample_tb, fixability)
                ingested += 1
    except OSError:
        return 0

    if ingested:
        logger.info("[automod] fallback ingested %d log records", ingested)
    return ingested


def _parse_log_line(line: str) -> dict | None:
    """Parse one JSON-line log record. Tolerates non-JSON lines."""
    line = line.strip()
    if not line or not line.startswith("{"):
        return None
    try:
        rec = json.loads(line)
    except json.JSONDecodeError:
        return None
    out = {
        "level": rec.get("level", ""),
        "message": rec.get("message", ""),
        "ts": _parse_ts(rec.get("timestamp")),
        "traceback": rec.get("exception", "") or rec.get("traceback", ""),
        "exc_class": None,
        "exc_message": "",
    }
    tb_text = out["traceback"]
    if tb_text:
        # The exception class we want is the LAST "ExcClass: message" line
        # in the traceback — for chained exceptions ("During handling of
        # the above exception, another exception occurred:"), the LAST
        # match is the WRAPPING exception, which is what bubbled through
        # jarvis code. The earlier matches are root causes (often
        # third-party) we can't fix directly.
        all_matches = list(re.finditer(
            r"^([A-Za-z_][A-Za-z0-9_.]*): (.*?)$",
            tb_text, re.MULTILINE,
        ))
        if all_matches:
            last = all_matches[-1]
            out["exc_class"] = last.group(1).split(".")[-1]
            out["exc_message"] = last.group(2)
    return out


def _parse_ts(ts: str | None) -> float:
    if not ts:
        return 0.0
    try:
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        return datetime.fromisoformat(ts).timestamp()
    except (ValueError, TypeError):
        return 0.0


def _frames_from_tb(tb_text: str) -> list[tuple[str, str]]:
    """Extract [(rel_path, method)] from a traceback string. Only
    jarvis-owned frames are returned."""
    out = []
    for m in _FRAME_RE.finditer(tb_text):
        filename, _line, method = m.group(1), m.group(2), m.group(3)
        if not _is_jarvis_owned(filename):
            continue
        idx = filename.find("src/voice-agent/")
        rel = filename[idx:] if idx >= 0 else filename
        out.append((rel, method))
    return out
