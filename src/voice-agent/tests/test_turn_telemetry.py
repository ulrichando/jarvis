import sqlite3
import tempfile
import time
from pathlib import Path

from pipeline.turn_telemetry import log_turn, init_db, report, _median_int, _parse_days_arg


def _seed(db_path, rows):
    """Helper: insert pre-shaped rows directly. Used by report() tests
    so we can pin ts_utc for the --days slicing test without sleeping.

    Row shape: (ts, user, jarvis, emotion, route, llm, voice, ttfw,
    audio, followup, fb, notes [, subagent])."""
    init_db(db_path)
    with sqlite3.connect(db_path) as c:
        # Detect whether rows include the subagent column (Phase 6+)
        first = rows[0] if rows else ()
        has_spec = len(first) >= 13
        if has_spec:
            c.executemany(
                """INSERT INTO turns
                   (ts_utc, user_text, jarvis_text, emotion, route, llm_used,
                    voice_used, ttfw_ms, total_audio_ms, user_followup_30s,
                    route_fallback, notes, subagent)
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
    # 2026-05-24: TASK was split into 5 sub-routes (DESKTOP/BROWSER/
    # CODE/FILES/OTHER). Seed one row per sub-route so the route-health
    # check's "no route receives <5% of total traffic" floor passes for
    # all 8 routes. Per-route distribution is roughly even (1/8 = 12.5%
    # each), comfortably above the 5% floor.
    rows = [
        # (ts, user, jarvis, emotion, route, llm, voice, ttfw, audio, followup, fb, notes)
        (now, "u1", "j1", "neutral",  "TASK_OTHER",   "g", "v", 500,  1000, 0, 0, ""),
        (now, "u2", "j2", "neutral",  "TASK_OTHER",   "g", "v", 1500, 1200, 0, 0, ""),
        (now, "u3", "j3", "curious",  "REASONING",   "g", "v", 800,  2000, 0, 0, ""),
        (now, "u4", "j4", "frustrated","EMOTIONAL",  "g", "v", 950,  1800, 1, 0, ""),
        (now, "u5", "j5", "excited",  "BANTER",      "g", "v", 200,  500,  0, 1, ""),
        (now, "u6", "j6", "neutral",  "TASK_DESKTOP","g", "v", 400,  900,  0, 0, ""),
        (now, "u7", "j7", "neutral",  "TASK_BROWSER","g", "v", 600,  900,  0, 0, ""),
        (now, "u8", "j8", "neutral",  "TASK_CODE",   "g", "v", 700,  900,  0, 0, ""),
        (now, "u9", "j9", "neutral",  "TASK_FILES",  "g", "v", 800,  900,  0, 0, ""),
    ]
    _seed(db, rows)
    out = report(db, ttfw_target_ms=1000)
    # Overall hit rate: 8 of 9 turns ≤ 1000ms = 88.888...%, rounds to 89%.
    assert "ttfw target hit-rate: 89%" in out
    # All 8 routes present so the health line is OK, not WARN
    assert "route health: OK" in out
    # Per-route lines exist for each of the 8 routes
    for label in (
        "BANTER",
        "TASK_DESKTOP", "TASK_BROWSER", "TASK_CODE", "TASK_FILES", "TASK_OTHER",
        "REASONING", "EMOTIONAL",
    ):
        assert label in out
    # Median is computed (TASK_OTHER row has 500 and 1500 → median 1000)
    assert "median=1000ms" in out
    # Fallback rate: 1 of 9 ≈ 11.1%
    assert "route-fallback rate: 11.1%" in out


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


# ── Phase 6: subagent column + report breakdown ─────────────────────


def test_log_turn_writes_subagent_column(tmp_path):
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
        subagent="desktop",
    )
    rows = sqlite3.connect(db_path).execute(
        "SELECT subagent FROM turns"
    ).fetchall()
    assert rows == [("desktop",)]


def test_log_turn_subagent_defaults_to_null(tmp_path):
    """When no handoff happened on a turn, subagent should be NULL —
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
        "SELECT subagent FROM turns"
    ).fetchone()
    assert row == (None,)


def test_init_db_migrates_existing_schema(tmp_path):
    """When init_db hits a pre-Phase-6 db (no subagent column),
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
        assert "subagent" in cols
        # Old row preserved
        cnt = c.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
        assert cnt == 1


def test_report_shows_subagent_distribution(tmp_path):
    db = tmp_path / "t.db"
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    rows = [
        # Two desktop, one planner, one browser, two supervisor (NULL subagent)
        (now, "u", "j", "neutral", "TASK", "g", "v", 600, 900, 0, 0, "", "desktop"),
        (now, "u", "j", "neutral", "TASK", "g", "v", 700, 1000, 0, 0, "", "desktop"),
        (now, "u", "j", "neutral", "REASONING", "g", "v", 1500, 2200, 0, 0, "", "planner"),
        (now, "u", "j", "neutral", "TASK", "g", "v", 800, 1100, 0, 0, "", "browser"),
        (now, "u", "j", "neutral", "BANTER", "g", "v", 300, 500, 0, 0, "", None),
        (now, "u", "j", "neutral", "BANTER", "g", "v", 250, 500, 0, 0, "", None),
    ]
    _seed(db, rows)
    out = report(db)
    assert "subagent usage:" in out
    # Expect to see all four buckets
    assert "desktop=2/6" in out
    assert "planner=1/6" in out
    assert "browser=1/6" in out
    assert "supervisor=2/6" in out


# ── 2026-05-05 cost-tracker columns (port from claude-code/cost-tracker.ts) ─


def test_init_db_adds_cost_columns(tmp_path):
    """Migration adds input_tokens, output_tokens, cost_usd,
    context_pressure to a pre-existing schema."""
    db = tmp_path / "telemetry.db"
    # Simulate a pre-2026-05-05 db: create the base schema without the
    # cost columns.
    with sqlite3.connect(db) as c:
        c.execute(
            """CREATE TABLE turns (
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
                notes TEXT,
                subagent TEXT,
                interrupted INTEGER DEFAULT 0
            )"""
        )
    # Migration runs.
    init_db(db)
    cols = {
        r[1]
        for r in sqlite3.connect(db).execute("PRAGMA table_info(turns)")
    }
    for new_col in ("input_tokens", "output_tokens", "cost_usd", "context_pressure"):
        assert new_col in cols, f"migration missed column {new_col}"


def test_log_turn_writes_cost_columns(tmp_path):
    db = tmp_path / "telemetry.db"
    init_db(db)
    log_turn(
        db_path=db,
        user_text="what time is it",
        jarvis_text="9:42",
        emotion="neutral",
        route="TASK",
        llm_used="llama-3.3-70b-versatile",
        voice_used="troy",
        ttfw_ms=120,
        total_audio_ms=200,
        user_followup_30s=False,
        route_fallback=False,
        input_tokens=14_523,
        output_tokens=42,
        cost_usd=0.00857,
        context_pressure="ok",
    )
    rows = sqlite3.connect(db).execute(
        "SELECT input_tokens, output_tokens, cost_usd, context_pressure FROM turns"
    ).fetchall()
    assert rows == [(14_523, 42, 0.00857, "ok")]


def test_log_turn_cost_columns_default_null(tmp_path):
    """Pre-existing call sites that don't pass the cost args should
    still write cleanly, with NULL cost columns."""
    db = tmp_path / "telemetry.db"
    init_db(db)
    log_turn(
        db_path=db,
        user_text="x", jarvis_text="y",
        emotion="neutral", route="TASK",
        llm_used="llama-3.3-70b-versatile", voice_used="troy",
        ttfw_ms=120, total_audio_ms=200,
        user_followup_30s=False, route_fallback=False,
    )
    row = sqlite3.connect(db).execute(
        "SELECT input_tokens, output_tokens, cost_usd, context_pressure FROM turns"
    ).fetchone()
    assert row == (None, None, None, None)


def test_report_includes_cost_section_when_priced(tmp_path):
    db = tmp_path / "telemetry.db"
    init_db(db)
    # Seed two priced turns + one unpriced.
    for i, (in_t, out_t, cost) in enumerate([
        (10_000, 30, 0.0059 + 0.0000237),
        (20_000, 60, 0.0118 + 0.0000474),
        (None, None, None),
    ]):
        log_turn(
            db_path=db,
            user_text=f"u{i}", jarvis_text=f"j{i}",
            emotion="neutral", route="TASK",
            llm_used="llama-3.3-70b-versatile", voice_used="troy",
            ttfw_ms=100, total_audio_ms=200,
            user_followup_30s=False, route_fallback=False,
            input_tokens=in_t, output_tokens=out_t, cost_usd=cost,
        )
    out = report(db_path=db)
    assert "cost:" in out
    assert "priced turns" in out
    assert "input=" in out and "tok" in out


def test_report_skips_cost_section_when_no_priced(tmp_path):
    """Fresh db / unpriced turns shouldn't print an empty cost section."""
    db = tmp_path / "telemetry.db"
    init_db(db)
    log_turn(
        db_path=db,
        user_text="x", jarvis_text="y",
        emotion="neutral", route="TASK",
        llm_used="llama-3.3-70b-versatile", voice_used="troy",
        ttfw_ms=100, total_audio_ms=200,
        user_followup_30s=False, route_fallback=False,
    )
    out = report(db_path=db)
    assert "cost:" not in out


def test_report_includes_context_pressure_when_present(tmp_path):
    db = tmp_path / "telemetry.db"
    init_db(db)
    for p in ("ok", "ok", "warn", "ok"):
        log_turn(
            db_path=db,
            user_text="x", jarvis_text="y",
            emotion="neutral", route="TASK",
            llm_used="llama-3.3-70b-versatile", voice_used="troy",
            ttfw_ms=100, total_audio_ms=200,
            user_followup_30s=False, route_fallback=False,
            context_pressure=p,
        )
    out = report(db_path=db)
    assert "context pressure:" in out
    assert "ok=3" in out
    assert "warn=1" in out


# ── Phase 2: memory_auto_extracted telemetry column ───────────────────


def test_log_turn_accepts_memory_auto_extracted_flag(tmp_path):
    """Phase 2 telemetry — boolean column tracking per-turn auto-
    extractor outcome. Lets us measure auto-extraction rate vs
    LLM-extraction rate over time."""
    from pipeline.turn_telemetry import init_db, log_turn
    db = tmp_path / "test_telemetry.db"
    init_db(str(db))
    log_turn(
        user_text="we charge $600/6mo",
        jarvis_text="Got it, sir.",
        emotion="neutral", route="TASK",
        llm_used="groq:llama-3.3-70b", voice_used="troy",
        ttfw_ms=200, total_audio_ms=1500,
        user_followup_30s=False, route_fallback=False,
        memory_auto_extracted=True,
        db_path=str(db),
    )
    import sqlite3
    n = sqlite3.connect(str(db)).execute(
        "SELECT memory_auto_extracted FROM turns"
    ).fetchone()
    assert n == (1,)


def test_log_turn_default_memory_auto_extracted_is_zero(tmp_path):
    """Default value when not specified — backward compat with existing
    log_turn callers that don't know about the new flag yet."""
    from pipeline.turn_telemetry import init_db, log_turn
    db = tmp_path / "test_telemetry.db"
    init_db(str(db))
    log_turn(
        user_text="hello",
        jarvis_text="Yes?",
        emotion="neutral", route="BANTER",
        llm_used="groq:llama-3.1-8b", voice_used="troy",
        ttfw_ms=100, total_audio_ms=500,
        user_followup_30s=False, route_fallback=False,
        db_path=str(db),
    )
    import sqlite3
    row = sqlite3.connect(str(db)).execute(
        "SELECT memory_auto_extracted FROM turns"
    ).fetchone()
    assert row == (0,)


def test_migration_adds_confab_check_state_column(tmp_path):
    """2026-05-19 — confab_check_state per-turn audit for the
    defense-in-depth fix. Spec: 2026-05-19-confab-defense-in-depth-design.md §5.4"""
    db = tmp_path / "tele.db"
    init_db(db)
    with sqlite3.connect(db) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(turns)")}
    assert "confab_check_state" in cols


def test_log_turn_persists_confab_check_state(tmp_path):
    db = tmp_path / "tele.db"
    init_db(db)
    log_turn(
        db_path=db,
        ts_utc="2026-05-19T03:00:00Z",
        user_text="open chrome",
        jarvis_text="Chrome's open.",
        route="TASK",
        confab_check_state="evidence_ok",
    )
    with sqlite3.connect(db) as conn:
        row = conn.execute(
            "SELECT confab_check_state FROM turns WHERE user_text='open chrome'"
        ).fetchone()
    assert row == ("evidence_ok",)
