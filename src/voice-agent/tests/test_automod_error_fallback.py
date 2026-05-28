"""Tests for the direct-tail fallback that seeds recurring_errors from
voice-agent.log when the handler hasn't populated it yet.
Spec 2026-05-27 Part 4."""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest


@pytest.fixture
def telemetry_db(tmp_path, monkeypatch):
    db_path = tmp_path / "telemetry.db"
    monkeypatch.setenv("JARVIS_TURN_TELEMETRY_DB", str(db_path))
    from pipeline.turn_telemetry import init_db
    init_db(db_path)
    yield db_path


@pytest.fixture
def fake_log(tmp_path, monkeypatch):
    """Monkeypatch LOG_PATH so the fallback reads from tmp."""
    p = tmp_path / "voice-agent.log"
    monkeypatch.setattr(
        "pipeline.automod.error_log_fallback.LOG_PATH", p,
    )
    return p


def _json_log_line(level: str, traceback_text: str | None,
                   ts_offset_sec: float = 0.0) -> str:
    """Build one JSON-line log record with the given level + tb."""
    ts_iso = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ",
        time.gmtime(time.time() + ts_offset_sec),
    )
    rec = {"level": level, "message": "x", "timestamp": ts_iso}
    if traceback_text:
        rec["exception"] = traceback_text
    return json.dumps(rec) + "\n"


_SAMPLE_TB = (
    'Traceback (most recent call last):\n'
    '  File "/home/ulrich/Documents/Projects/jarvis/src/voice-agent/jarvis_agent.py", line 42, in handle\n'
    '    raise ValueError("bad input")\n'
    'ValueError: bad input\n'
)


def test_no_op_when_table_has_rows(telemetry_db, fake_log):
    """Populates only when the table is empty. If a handler already
    inserted rows, the fallback must NOT touch anything."""
    fake_log.write_text(_json_log_line("ERROR", _SAMPLE_TB), encoding="utf-8")
    with sqlite3.connect(telemetry_db) as c:
        c.execute("""INSERT INTO recurring_errors
                     (signature, exc_class, first_seen, last_seen, count,
                      frames_json)
                     VALUES (?, ?, ?, ?, ?, ?)""",
                  ("preexisting", "RuntimeError",
                   "2026-05-27T00:00:00Z", "2026-05-27T00:00:00Z",
                   5, "[]"))
    from pipeline.automod.error_log_fallback import populate_from_log_if_empty
    with sqlite3.connect(telemetry_db) as c:
        inserted = populate_from_log_if_empty(c)
    assert inserted == 0
    with sqlite3.connect(telemetry_db) as c:
        rows = c.execute("SELECT signature FROM recurring_errors").fetchall()
    assert ("preexisting",) in rows
    assert len(rows) == 1, "no new rows inserted"


def test_parses_json_error_records_with_traceback(telemetry_db, fake_log):
    """A single ERROR record with a jarvis-owned traceback → 1 row."""
    fake_log.write_text(_json_log_line("ERROR", _SAMPLE_TB), encoding="utf-8")
    from pipeline.automod.error_log_fallback import populate_from_log_if_empty
    with sqlite3.connect(telemetry_db) as c:
        inserted = populate_from_log_if_empty(c)
    assert inserted == 1
    with sqlite3.connect(telemetry_db) as c:
        rows = c.execute(
            "SELECT exc_class, count FROM recurring_errors"
        ).fetchall()
    assert rows == [("ValueError", 1)]


def test_skips_records_outside_24h_window(telemetry_db, fake_log):
    """Records with timestamp older than 24h must be ignored."""
    old = _json_log_line("ERROR", _SAMPLE_TB,
                         ts_offset_sec=-25 * 3600)  # 25h ago
    fresh = _json_log_line("ERROR", _SAMPLE_TB,
                           ts_offset_sec=-1 * 3600)  # 1h ago
    fake_log.write_text(old + fresh, encoding="utf-8")
    from pipeline.automod.error_log_fallback import populate_from_log_if_empty
    with sqlite3.connect(telemetry_db) as c:
        inserted = populate_from_log_if_empty(c)
    # Same signature for both records → 1 row, count incremented only
    # for the in-window record.
    assert inserted == 1
    with sqlite3.connect(telemetry_db) as c:
        rows = c.execute(
            "SELECT count FROM recurring_errors"
        ).fetchall()
    assert rows == [(1,)], (
        f"only the fresh record should have been ingested; got {rows}"
    )


def test_tolerates_malformed_json_lines(telemetry_db, fake_log):
    """Garbled lines must not abort the whole scan."""
    body = (
        "not-json-at-all\n"
        "{broken json\n"
        + _json_log_line("ERROR", _SAMPLE_TB)
        + "\n"  # blank line
        + _json_log_line("INFO", None)  # non-error
    )
    fake_log.write_text(body, encoding="utf-8")
    from pipeline.automod.error_log_fallback import populate_from_log_if_empty
    with sqlite3.connect(telemetry_db) as c:
        inserted = populate_from_log_if_empty(c)
    assert inserted == 1


def test_no_log_file_no_op(telemetry_db, tmp_path, monkeypatch):
    """If voice-agent.log doesn't exist, return 0 cleanly."""
    monkeypatch.setattr(
        "pipeline.automod.error_log_fallback.LOG_PATH",
        tmp_path / "does-not-exist.log",
    )
    from pipeline.automod.error_log_fallback import populate_from_log_if_empty
    with sqlite3.connect(telemetry_db) as c:
        inserted = populate_from_log_if_empty(c)
    assert inserted == 0


def test_chained_exception_uses_wrapping_class(telemetry_db, fake_log):
    """Chained traceback ('During handling of the above...') → the
    LAST exception line (the wrapping one) is what gets signatured,
    not the first (the root cause)."""
    chained_tb = (
        'Traceback (most recent call last):\n'
        '  File "/home/ulrich/Documents/Projects/jarvis/src/voice-agent/jarvis_agent.py", line 100, in inner\n'
        '    raise ValueError("root cause")\n'
        'ValueError: root cause\n'
        '\n'
        'During handling of the above exception, another exception occurred:\n'
        '\n'
        'Traceback (most recent call last):\n'
        '  File "/home/ulrich/Documents/Projects/jarvis/src/voice-agent/jarvis_agent.py", line 200, in outer\n'
        '    raise RuntimeError("wrapping error")\n'
        'RuntimeError: wrapping error\n'
    )
    fake_log.write_text(_json_log_line("ERROR", chained_tb), encoding="utf-8")
    from pipeline.automod.error_log_fallback import populate_from_log_if_empty
    with sqlite3.connect(telemetry_db) as c:
        inserted = populate_from_log_if_empty(c)
    assert inserted == 1
    with sqlite3.connect(telemetry_db) as c:
        rows = c.execute(
            "SELECT exc_class FROM recurring_errors"
        ).fetchall()
    # Must be the WRAPPING exception (RuntimeError), NOT the root cause (ValueError).
    assert rows == [("RuntimeError",)], (
        f"expected RuntimeError (wrapping), got {rows[0][0]} — chained "
        "exception handling is selecting the wrong line"
    )
