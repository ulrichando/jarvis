import sqlite3
import tempfile
import time
from pathlib import Path

from turn_telemetry import log_turn, init_db, report, _median_int, _parse_days_arg


def _seed(db_path, rows):
    """Helper: insert pre-shaped rows directly. Used by report() tests
    so we can pin ts_utc for the --days slicing test without sleeping.

    Row shape: (ts, user, jarvis, emotion, route, llm, voice, ttfw,
    audio, followup, fb, notes [, specialist])."""
    init_db(db_path)
    with sqlite3.connect(db_path) as c:
        # Detect whether rows include the specialist column (Phase 6+)
        first = rows[0] if rows else ()
        has_spec = len(first) >= 13
        if has_spec:
            c.executemany(
                """INSERT INTO turns
                   (ts_utc, user_text, jarvis_text, emotion, route, llm_used,
                    voice_used, ttfw_ms, total_audio_ms, user_followup_30s,
                    route_fallback, notes, specialist)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )
        else:
            c.executemany(
                """INSERT INTO turns
                   (ts_utc, user_text, jarvis_text, emotion, route, llm_used,
                    voice_used, ttfw_ms, total_audio_ms, user_followup_30s,
                    route_fallback, notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )


def test_log_turn_writes_row(tmp_path):
    db_path = tmp_path / "telemetry.db"
    init_db(db_path)
    log_turn(
        db_path=db_path,
        user_text="what time is it",
        jarvis_text="nine forty-five PM",
        emotion="neutral",
        route="TASK",
        llm_used="groq:llama-3.3-70b-versatile",
        voice_used="bm_george",
        ttfw_ms=850,
        total_audio_ms=1500,
        user_followup_30s=False,
        route_fallback=False,
    )
    rows = sqlite3.connect(db_path).execute("SELECT route, llm_used, ttfw_ms FROM turns").fetchall()
    assert rows == [("TASK", "groq:llama-3.3-70b-versatile", 850)]


def test_log_turn_silently_swallows_disk_error(monkeypatch, tmp_path):
    bogus = tmp_path / "doesnotexist" / "x.db"  # parent missing
    # No init_db called → log_turn must not raise
    log_turn(
        db_path=bogus,
        user_text="x", jarvis_text="y",
        emotion="neutral", route="TASK",
        llm_used="x", voice_used="x",
        ttfw_ms=0, total_audio_ms=0,
        user_followup_30s=False, route_fallback=False,
    )


# ── report() tests ─────────────────────────────────────────────────────


def test_median_int_handles_empty_and_odd_and_even():
    assert _median_int([]) is None
    assert _median_int([5]) == 5
    assert _median_int([1, 2, 3]) == 2
    assert _median_int([1, 2, 3, 4]) == 2  # int division on (2+3)//2
    # Filters None
    assert _median_int([None, 7, None, 11]) == 9


def test_report_no_db_returns_friendly_string(tmp_path):
    assert report(tmp_path / "missing.db") == "no telemetry yet"


def test_report_empty_db_says_zero_turns(tmp_path):
    db = tmp_path / "t.db"
    init_db(db)
    out = report(db)
    assert "total turns=0" in out


def test_report_includes_ttfw_hit_rate_per_route(tmp_path):
    db = tmp_path / "t.db"
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    rows = [
        # (ts, user, jarvis, emotion, route, llm, voice, ttfw, audio, followup, fb, notes)
        (now, "u1", "j1", "neutral",  "TASK",      "g", "v", 500,  1000, 0, 0, ""),
        (now, "u2", "j2", "neutral",  "TASK",      "g", "v", 1500, 1200, 0, 0, ""),
        (now, "u3", "j3", "curious",  "REASONING", "g", "v", 800,  2000, 0, 0, ""),
        (now, "u4", "j4", "frustrated","EMOTIONAL","g", "v", 950,  1800, 1, 0, ""),
        (now, "u5", "j5", "excited",  "BANTER",    "g", "v", 200,  500,  0, 1, ""),
    ]
    _seed(db, rows)
    out = report(db, ttfw_target_ms=1000)
    # Overall hit rate: 4 of 5 turns ≤ 1000ms
    assert "ttfw target hit-rate: 80%" in out
    # All four routes present so the health line is OK, not WARN
    assert "route health: OK" in out
    # Per-route lines exist for each
    for label in ("BANTER", "TASK", "REASONING", "EMOTIONAL"):
        assert label in out
    # Median is computed (the TASK row has 500 and 1500 → median 1000)
    assert "median=1000ms" in out
    # Fallback rate: 1 of 5 = 20%
    assert "route-fallback rate: 20.0%" in out


def test_report_flags_under_served_route(tmp_path):
    db = tmp_path / "t.db"
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    # 30 TASK rows, no other routes → all three other routes have zero,
    # router collapsed onto TASK only.
    rows = [(now, "u", "j", "neutral", "TASK", "g", "v", 800, 1000, 0, 0, "")] * 30
    _seed(db, rows)
    out = report(db)
    assert "route health: WARN" in out
    assert "route BANTER has no turns" in out
    assert "route REASONING has no turns" in out
    assert "route EMOTIONAL has no turns" in out


def test_report_days_slice_excludes_old_turns(tmp_path):
    db = tmp_path / "t.db"
    old = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 10 * 86400)
    )
    new = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    rows = [
        (old, "u", "j", "neutral", "TASK", "g", "v", 500, 1000, 0, 0, ""),
        (new, "u", "j", "neutral", "TASK", "g", "v", 600, 1000, 0, 0, ""),
    ]
    _seed(db, rows)
    out = report(db, days=7)
    assert "scope=last 7d" in out
    assert "total turns=1" in out  # only the recent row counted


def test_parse_days_arg():
    assert _parse_days_arg(["x.py", "--report"]) is None
    assert _parse_days_arg(["x.py", "--report", "--days", "7"]) == 7
    assert _parse_days_arg(["x.py", "--days", "0"]) is None        # bad
    assert _parse_days_arg(["x.py", "--days", "abc"]) is None       # bad
    assert _parse_days_arg(["x.py", "--days"]) is None              # missing


# ── Phase 6: specialist column + report breakdown ─────────────────────


def test_log_turn_writes_specialist_column(tmp_path):
    db_path = tmp_path / "telemetry.db"
    init_db(db_path)
    log_turn(
        db_path=db_path,
        user_text="open chrome",
        jarvis_text="On it, sir.",
        emotion="neutral",
        route="TASK",
        llm_used="groq:llama-3.3-70b",
        voice_used="bm_george",
        ttfw_ms=600,
        total_audio_ms=1200,
        user_followup_30s=False,
        route_fallback=False,
        specialist="desktop",
    )
    rows = sqlite3.connect(db_path).execute(
        "SELECT specialist FROM turns"
    ).fetchall()
    assert rows == [("desktop",)]


def test_log_turn_specialist_defaults_to_null(tmp_path):
    """When no handoff happened on a turn, specialist should be NULL —
    those rows show up under 'supervisor' in the report."""
    db_path = tmp_path / "telemetry.db"
    init_db(db_path)
    log_turn(
        db_path=db_path,
        user_text="what time is it",
        jarvis_text="Nine thirty.",
        emotion="neutral", route="TASK",
        llm_used="g", voice_used="v",
        ttfw_ms=400, total_audio_ms=900,
        user_followup_30s=False, route_fallback=False,
    )
    row = sqlite3.connect(db_path).execute(
        "SELECT specialist FROM turns"
    ).fetchone()
    assert row == (None,)


def test_init_db_migrates_existing_schema(tmp_path):
    """When init_db hits a pre-Phase-6 db (no specialist column),
    it should add the column without dropping data."""
    db_path = tmp_path / "telemetry.db"
    # Build an old-shape table BEFORE calling init_db
    with sqlite3.connect(db_path) as c:
        c.executescript("""
            CREATE TABLE turns (
                id INTEGER PRIMARY KEY,
                ts_utc TEXT NOT NULL,
                user_text TEXT NOT NULL,
                jarvis_text TEXT NOT NULL,
                emotion TEXT, route TEXT, llm_used TEXT, voice_used TEXT,
                ttfw_ms INTEGER, total_audio_ms INTEGER,
                user_followup_30s INTEGER, route_fallback INTEGER, notes TEXT
            );
        """)
        c.execute(
            "INSERT INTO turns (ts_utc, user_text, jarvis_text) VALUES (?, ?, ?)",
            ("2026-04-29T00:00:00Z", "old turn", "old reply"),
        )

    # Now run the migration via init_db
    init_db(db_path)

    with sqlite3.connect(db_path) as c:
        cols = {r[1] for r in c.execute("PRAGMA table_info(turns)")}
        assert "specialist" in cols
        # Old row preserved
        cnt = c.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
        assert cnt == 1


def test_report_shows_specialist_distribution(tmp_path):
    db = tmp_path / "t.db"
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    rows = [
        # Two desktop, one planner, one browser, two supervisor (NULL specialist)
        (now, "u", "j", "neutral", "TASK", "g", "v", 600, 900, 0, 0, "", "desktop"),
        (now, "u", "j", "neutral", "TASK", "g", "v", 700, 1000, 0, 0, "", "desktop"),
        (now, "u", "j", "neutral", "REASONING", "g", "v", 1500, 2200, 0, 0, "", "planner"),
        (now, "u", "j", "neutral", "TASK", "g", "v", 800, 1100, 0, 0, "", "browser"),
        (now, "u", "j", "neutral", "BANTER", "g", "v", 300, 500, 0, 0, "", None),
        (now, "u", "j", "neutral", "BANTER", "g", "v", 250, 500, 0, 0, "", None),
    ]
    _seed(db, rows)
    out = report(db)
    assert "specialist usage:" in out
    # Expect to see all four buckets
    assert "desktop=2/6" in out
    assert "planner=1/6" in out
    assert "browser=1/6" in out
    assert "supervisor=2/6" in out
