import sqlite3
import tempfile
from pathlib import Path

from turn_telemetry import log_turn, init_db


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
