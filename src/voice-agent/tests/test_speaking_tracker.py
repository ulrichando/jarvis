"""Tests for pipeline/speaking_tracker.py — process-local record of the text
JARVIS is currently / was just speaking.

The TTS shim has no AgentSession handle, so it feeds this tracker instead of a
session attribute. One LiveKit worker job handles one session, so process-local
state is session-scoped in practice. Two reads:
  - current_speaking_text()  — what JARVIS is saying NOW (interrupt consumer)
  - recent_speaking_text(ttl) — what JARVIS just said, within ttl of speech end
                                (phantom-turn consumer, since a finalized echo
                                 turn arrives AFTER speech ends)

Spec: docs/superpowers/specs/2026-05-20-echo-aware-bargein-gate-design.md
"""
from __future__ import annotations


def test_note_accumulates_into_current():
    from pipeline import speaking_tracker as st
    st.reset()
    st.note_speaking("the weather is")
    st.note_speaking("nice today")
    cur = st.current_speaking_text()
    assert "weather" in cur and "nice today" in cur


def test_mark_ended_clears_current_keeps_recent():
    from pipeline import speaking_tracker as st
    st.reset()
    st.note_speaking("open the pod bay doors")
    st.mark_speech_ended()
    assert st.current_speaking_text() == ""               # live buffer cleared
    assert "pod bay" in st.recent_speaking_text(ttl_s=2.0)  # snapshot retained


def test_recent_expires_after_ttl(monkeypatch):
    from pipeline import speaking_tracker as st
    st.reset()
    clock = [1000.0]
    monkeypatch.setattr(st.time, "monotonic", lambda: clock[0])
    st.note_speaking("hello there")
    st.mark_speech_ended()                  # ended at t=1000
    clock[0] = 1001.0                        # +1s, within ttl
    assert "hello" in st.recent_speaking_text(ttl_s=2.0)
    clock[0] = 1003.0                        # +3s, past ttl
    assert st.recent_speaking_text(ttl_s=2.0) == ""


def test_recent_returns_live_text_while_still_speaking():
    from pipeline import speaking_tracker as st
    st.reset()
    st.note_speaking("still talking now")
    # no mark_speech_ended yet → recent falls back to the live buffer
    assert "talking" in st.recent_speaking_text(ttl_s=2.0)


def test_new_speech_after_end_starts_fresh():
    from pipeline import speaking_tracker as st
    st.reset()
    st.note_speaking("first utterance")
    st.mark_speech_ended()
    st.note_speaking("second utterance")
    cur = st.current_speaking_text()
    assert "second" in cur and "first" not in cur


def test_reset_clears_everything():
    from pipeline import speaking_tracker as st
    st.note_speaking("stuff")
    st.mark_speech_ended()
    st.reset()
    assert st.current_speaking_text() == ""
    assert st.recent_speaking_text(ttl_s=999) == ""
