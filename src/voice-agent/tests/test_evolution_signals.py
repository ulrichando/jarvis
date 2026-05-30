"""Tests for the evolution telemetry reader + pure signal extraction."""
from __future__ import annotations

import sqlite3

from evolution import db_read
from evolution.db_read import _COLS


def _make_db(tmp_path, rows, *, cols=None):
    """Build a minimal turns table and insert rows.

    `cols` defaults to the 6 columns the reader cares about. `rows` is a list of
    dicts keyed by a subset of `cols`; missing keys are inserted as NULL.
    """
    cols = list(cols if cols is not None else _COLS)
    db_path = tmp_path / "telemetry.db"
    con = sqlite3.connect(str(db_path))
    col_defs = ", ".join(f"{c} TEXT" if c not in ("ttfw_ms", "interrupted") else f"{c} INTEGER"
                         for c in cols)
    con.execute(f"CREATE TABLE turns ({col_defs})")
    for row in rows:
        keys = [c for c in cols]
        vals = [row.get(c) for c in keys]
        placeholders = ", ".join("?" for _ in keys)
        con.execute(f"INSERT INTO turns ({', '.join(keys)}) VALUES ({placeholders})", vals)
    con.commit()
    con.close()
    return db_path


# --- Task 2: db_read ---------------------------------------------------------

def test_read_turns_roundtrip(tmp_path):
    rows = [
        {"ts_utc": "2026-05-30T00:00:01Z", "route": "task", "ttfw_ms": 100,
         "interrupted": 0, "confab_check_state": "clean", "user_text": "one"},
        {"ts_utc": "2026-05-30T00:00:02Z", "route": "banter", "ttfw_ms": 200,
         "interrupted": 0, "confab_check_state": "clean", "user_text": "two"},
        {"ts_utc": "2026-05-30T00:00:03Z", "route": "task", "ttfw_ms": 300,
         "interrupted": 1, "confab_check_state": "unchecked", "user_text": "three"},
    ]
    db_path = _make_db(tmp_path, rows)
    out = db_read.read_turns(db_path)
    assert len(out) == 3
    for d in out:
        for c in _COLS:
            assert c in d


def test_read_turns_since_filter(tmp_path):
    rows = [
        {"ts_utc": "2026-05-30T00:00:01Z", "user_text": "early"},
        {"ts_utc": "2026-05-30T00:00:05Z", "user_text": "mid"},
        {"ts_utc": "2026-05-30T00:00:09Z", "user_text": "late"},
    ]
    db_path = _make_db(tmp_path, rows)
    out = db_read.read_turns(db_path, since="2026-05-30T00:00:05Z")
    texts = [r["user_text"] for r in out]
    assert texts == ["mid", "late"]


def test_read_turns_tolerates_missing_columns(tmp_path):
    rows = [{"ts_utc": "2026-05-30T00:00:01Z"}, {"ts_utc": "2026-05-30T00:00:02Z"}]
    db_path = _make_db(tmp_path, rows, cols=["ts_utc"])
    out = db_read.read_turns(db_path)
    assert len(out) == 2
    for d in out:
        for c in _COLS:
            assert c in d
        assert d["route"] is None
        assert d["ttfw_ms"] is None
        assert d["user_text"] is None


def test_read_turns_no_table_returns_empty(tmp_path):
    db_path = tmp_path / "notable.db"
    con = sqlite3.connect(str(db_path))
    con.execute("CREATE TABLE other (x INTEGER)")
    con.commit()
    con.close()
    assert db_read.read_turns(db_path) == []


def test_read_missing_db_returns_empty(tmp_path):
    assert db_read.read_turns(tmp_path / "nope.db") == []


# --- Task 3: pure signal extraction -----------------------------------------

from evolution.signals import compute_signals


def test_empty_window():
    sig = compute_signals([])
    assert sig.n_turns == 0
    assert sig.confab_quality == 1.0
    assert sig.clean_action_rate == 1.0
    assert sig.reask_rate == 0.0


def test_reask_detected():
    turns = [
        {"user_text": "open the browser"},
        {"user_text": "open the browser"},
        {"user_text": "open the browser"},
    ]
    sig = compute_signals(turns)
    assert sig.reask_rate > 0
    # 2nd and 3rd are reasks of an earlier in-window utterance → 2 reasks of 3 turns
    assert sig.reask_rate == 2 / 3


def test_confab_quality():
    turns = [
        {"confab_check_state": "clean"},
        {"confab_check_state": "clean_no_claim"},
        {"confab_check_state": "caught_t1_passed"},
        {"confab_check_state": "unchecked"},
    ]
    sig = compute_signals(turns)
    assert sig.n_checked == 3
    assert sig.confab_quality == (2 + 0.5) / 3


def test_confab_failure_drags_quality():
    turns = [
        {"confab_check_state": "clean"},
        {"confab_check_state": "no_text_filler"},
    ]
    sig = compute_signals(turns)
    assert sig.confab_quality == (1 + 0) / 2


def test_clean_action_rate():
    turns = [
        {"confab_check_state": "clean_tool_called"},
        {"confab_check_state": "clean_tool_called"},
        {"confab_check_state": "clean_tool_called"},
        {"confab_check_state": "no_text_t1_passed"},
    ]
    sig = compute_signals(turns)
    assert sig.clean_action_rate == 3 / 4


def test_median_ttfw():
    turns = [{"ttfw_ms": 100}, {"ttfw_ms": 200}, {"ttfw_ms": 300}]
    assert compute_signals(turns).median_ttfw_ms == 200.0
    none_turns = [{"ttfw_ms": None}, {"ttfw_ms": 0}]
    assert compute_signals(none_turns).median_ttfw_ms == 0.0


def test_interruption_rate():
    turns = [
        {"interrupted": 1},
        {"interrupted": 0},
        {"interrupted": 0},
        {"interrupted": 0},
    ]
    assert compute_signals(turns).interruption_rate == 0.25
