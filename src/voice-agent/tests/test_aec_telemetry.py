"""AEC telemetry columns on the turns table (2026-05-19 echo-cancel cascade)."""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipeline.turn_telemetry import init_db, log_turn


def test_migration_adds_aec_columns(tmp_path):
    db = tmp_path / "tele.db"
    init_db(db)
    with sqlite3.connect(db) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(turns)")}
    for c in ("aec_layer1_active", "aec_layer2_aec_active", "aec_layer3_active",
              "output_profile", "apm_delay_ms_p50", "dtln_latency_ms_p95"):
        assert c in cols, f"missing column {c}"


def test_log_turn_persists_aec_fields(tmp_path):
    db = tmp_path / "tele.db"
    init_db(db)
    log_turn(
        db_path=db, user_text="hi", jarvis_text="Yes?", route="BANTER",
        aec_layer1_active=1, aec_layer2_aec_active=0, aec_layer3_active=1,
        output_profile="speakers", apm_delay_ms_p50=42, dtln_latency_ms_p95=3.1,
    )
    with sqlite3.connect(db) as conn:
        row = conn.execute(
            "SELECT aec_layer1_active, aec_layer3_active, output_profile, "
            "apm_delay_ms_p50, dtln_latency_ms_p95 FROM turns "
            "WHERE user_text='hi'"
        ).fetchone()
    assert row == (1, 1, "speakers", 42, 3.1)
