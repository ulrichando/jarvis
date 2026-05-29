"""turn_telemetry schema must include user_lang. Additive column —
default 'en' so back-compat with rows written before the migration."""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path


def _open_with_schema(db_path: Path) -> sqlite3.Connection:
    """Open a fresh telemetry DB and apply the schema migrations.

    Importing turn_telemetry and running the project's startup-schema
    function is the cleanest path. The project's schema-init function
    is `init_db`, which accepts an explicit db_path."""
    from pipeline import turn_telemetry
    turn_telemetry.init_db(db_path)
    conn = sqlite3.connect(db_path)
    return conn


def test_turns_table_has_user_lang_column():
    """Open a fresh telemetry DB and confirm user_lang is in the
    turns table schema with a default of 'en'."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "telemetry.db"
        conn = _open_with_schema(db_path)
        try:
            cols = {row[1]: row for row in conn.execute("PRAGMA table_info(turns)")}
            assert "user_lang" in cols, (
                "turns table missing user_lang column"
            )
            # Default should be 'en' (column 4 in PRAGMA table_info is
            # dflt_value; SQLite stores it as a quoted string literal,
            # so the substring check is robust to quoting).
            default = cols["user_lang"][4]
            assert default is not None and "en" in str(default), (
                f"user_lang default should be 'en', got {default!r}"
            )
        finally:
            conn.close()


def test_existing_db_gets_user_lang_added_idempotently():
    """If the column already exists, the schema-init function must be
    a no-op (no exception). Migration runs on every voice-agent startup."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "telemetry.db"
        conn = _open_with_schema(db_path)
        conn.close()
        # Second call must be idempotent.
        from pipeline import turn_telemetry
        turn_telemetry.init_db(db_path)  # must not raise
        conn = sqlite3.connect(db_path)
        try:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(turns)")}
            assert "user_lang" in cols
        finally:
            conn.close()


def test_log_turn_writes_user_lang():
    """log_turn must persist the user_lang value passed in."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "telemetry.db"
        from pipeline.turn_telemetry import init_db, log_turn
        init_db(db_path)
        log_turn(
            db_path=db_path,
            user_text="bonjour",
            jarvis_text="oui",
            user_lang="fr",
        )
        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute(
                "SELECT user_lang FROM turns ORDER BY id DESC LIMIT 1"
            ).fetchone()
            assert row is not None
            assert row[0] == "fr", f"expected 'fr', got {row[0]!r}"
        finally:
            conn.close()


def test_log_turn_defaults_user_lang_to_en():
    """When user_lang is not supplied, the column must default to 'en'."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "telemetry.db"
        from pipeline.turn_telemetry import init_db, log_turn
        init_db(db_path)
        log_turn(
            db_path=db_path,
            user_text="hello",
            jarvis_text="yes",
            # user_lang omitted — should default to 'en'
        )
        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute(
                "SELECT user_lang FROM turns ORDER BY id DESC LIMIT 1"
            ).fetchone()
            assert row is not None
            assert row[0] == "en", f"expected 'en', got {row[0]!r}"
        finally:
            conn.close()
