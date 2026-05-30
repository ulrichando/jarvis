"""Tests for the evolution back-test harness (calibration anchor).

The calibration test is the load-bearing one: on synthetic good/bad windows the
fitness function MUST score the good window above the bad one AND pass the good
window's guardrails while vetoing the bad one. If this ever fails, the fitness
function is mis-calibrated and nothing may depend on it.
"""
from __future__ import annotations

import sqlite3

from evolution import backtest
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


def test_bad_window_scores_below_good(tmp_path):
    """Calibration anchor: a known-bad window must score below a known-good one,
    and the good window passes while the bad one is vetoed."""
    good_rows = []
    for i in range(10):
        good_rows.append({
            "ts_utc": f"2026-05-29T00:00:{i:02d}Z",
            "route": "task",
            "ttfw_ms": 900,
            "interrupted": 0,
            "confab_check_state": "clean",
            "user_text": f"distinct good utterance number {i}",
        })

    bad_rows = []
    for i in range(10):
        # Repeated user_text drives reask_rate high; high ttfw drags latency;
        # a couple of no_text_filler failures drag confab quality.
        bad_rows.append({
            "ts_utc": f"2026-05-30T00:00:{i:02d}Z",
            "route": "task",
            "ttfw_ms": 3000,
            "interrupted": 1 if i % 2 == 0 else 0,
            "confab_check_state": "no_text_filler" if i < 2 else "clean",
            "user_text": "open the browser please",
        })

    db_path = _make_db(tmp_path, good_rows + bad_rows)

    results = backtest.compare_windows(
        [
            ("good", "2026-05-29T00:00:00Z", "2026-05-29T23:59:59Z"),
            ("bad", "2026-05-30T00:00:00Z", "2026-05-30T23:59:59Z"),
        ],
        db_path=db_path,
    )
    by_label = {r["label"]: r for r in results}
    good = by_label["good"]
    bad = by_label["bad"]

    assert good["composite"] > bad["composite"]
    assert good["passed"] and not bad["passed"]


def test_score_window_empty_db_safe(tmp_path):
    """An empty DB file (no turns table) must not crash and must not pass."""
    db_path = tmp_path / "empty.db"
    con = sqlite3.connect(str(db_path))
    con.execute("CREATE TABLE other (x INTEGER)")
    con.commit()
    con.close()

    reading = backtest.score_window(db_path)
    assert reading.passed is False
    assert reading.n_turns == 0
