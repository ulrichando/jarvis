"""format_memories_for_prompt must NOT mutate use_count — injecting a
memory into the prompt is not evidence it was useful. (2026-05-20 fix
for the inject→bump rich-get-richer loop.)"""
import sqlite3
import sys
import time
from pathlib import Path

import pytest

_hub = Path(__file__).parent.parent.parent / "hub"
if not (_hub / "server.py").exists():
    pytest.skip("src/hub/ not present in this checkout", allow_module_level=True)
sys.path.insert(0, str(_hub))
import server  # noqa: E402


@pytest.fixture
def seeded_db(tmp_path, monkeypatch):
    db = tmp_path / "state.db"
    server.bootstrap_schema(db)
    monkeypatch.setenv("JARVIS_HUB_DB", str(db))
    now = int(time.time() * 1000)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO memories (memory_id, content, category, source, "
        "source_session_id, created_ts, updated_ts, last_used_ts, use_count) "
        "VALUES ('m1','Ulrich runs Pretva','user','voice',NULL,?,?,NULL,0)",
        (now, now),
    )
    conn.commit()
    conn.close()
    return db


def test_format_for_prompt_does_not_bump_use_count(seeded_db):
    from tools.memory import format_memories_for_prompt
    format_memories_for_prompt(top_n=8)
    conn = sqlite3.connect(seeded_db)
    uc = conn.execute("SELECT use_count FROM memories WHERE memory_id='m1'").fetchone()[0]
    conn.close()
    assert uc == 0, "injection must not increment use_count"
