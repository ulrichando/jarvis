# src/voice-agent/tests/test_denial_detector.py
"""Tests for the output-rail denial detector.

Watches the supervisor LLM's outgoing assistant text. If the text
matches the denial pattern AND no memory write/read fired this turn,
the detector suppresses the reply and signals a re-roll with forced
tool_choice.

JARVIS-original — no published precedent for capability-denial
specifically. Closest analog is LLM-Guard's NoRefusal scanner.
"""
from __future__ import annotations
import pytest
from sanitizers.denial_detector import is_capability_denial


@pytest.mark.parametrize("text", [
    "I'm a conversational AI, I don't retain information between conversations.",
    "I'm just an AI assistant, I can't remember between sessions.",
    "I'm afraid I don't have the ability to store or recall individual names or memories.",
    "I'm a language model, I don't retain information about individual users.",
    "I won't be able to recall it later — I don't have memory.",
    "Each time you interact with me, it's a new conversation, I don't store anything.",
])
def test_matches_capability_denials(text):
    assert is_capability_denial(text) is True


@pytest.mark.parametrize("text", [
    "Of course, sir.",
    "I can't open a tab — that's a desktop task.",            # tool refusal, not memory
    "I can't generate physical money.",                        # legitimate inability
    "Lizzy, sir.",                                             # successful recall reply
    "I don't have that yet, sir — want me to remember it now?", # honest empty
    "I'm not able to find what you mentioned.",                # vague but not a denial
    "I haven't been told that yet.",                           # honest empty (different shape)
])
def test_does_not_match_non_denials(text):
    assert is_capability_denial(text) is False


def test_install_is_idempotent():
    """install() must be safe to call multiple times (matches the
    existing sanitizer convention)."""
    import sanitizers.denial_detector as dd
    dd.install()
    dd.install()  # should not raise / should not double-patch


# ── Replay rails — persisted denials never reach the LLM again ─────────
# The voice-agent-lk regression (fix fa7e7080): a persisted "I have no
# memory" reply got replayed into later sessions, teaching the agent to
# keep denying it remembers. The conversations.db rails are pinned in
# test_conversation_store.py; this covers the telemetry-backed
# session_search tool, whose DB deliberately keeps the denial rows
# (faithful ops log) but must REDACT them on read.

_DENIAL = (
    "I'm a conversational AI, I don't retain information between "
    "conversations, so each session starts fresh."
)


def test_session_search_redacts_persisted_denial(tmp_path, monkeypatch):
    import json
    import sqlite3

    import tools.session_search as ss

    db = tmp_path / "turn_telemetry.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE turns ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, ts_utc TEXT NOT NULL, "
            "user_text TEXT NOT NULL, jarvis_text TEXT NOT NULL, "
            "emotion TEXT, route TEXT, llm_used TEXT)"
        )
        conn.execute(
            "INSERT INTO turns (ts_utc, user_text, jarvis_text) VALUES "
            "(datetime('now'), 'do you remember our conversations?', ?)",
            (_DENIAL,),
        )
        conn.execute(
            "INSERT INTO turns (ts_utc, user_text, jarvis_text) VALUES "
            "(datetime('now'), 'weather?', "
            "'Sunny conversations aside, 22 degrees.')",
        )
    monkeypatch.setattr(ss, "_TELEMETRY_DB", db)

    out = json.loads(ss._handle_session_search({"query": "conversations"}))
    assert out["status"] == "ok"
    texts = [r["jarvis_text"] for r in out["results"]]
    assert all("don't retain" not in t for t in texts)  # denial redacted
    assert any("22 degrees" in t for t in texts)        # benign row intact
