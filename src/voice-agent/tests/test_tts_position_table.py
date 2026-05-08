"""Tests for _record_synthesis — the helper that appends to the
session's TTS position table after each synthesize() call.

Spec: docs/superpowers/specs/2026-05-07-barge-in-truncation-design.md
"""
from __future__ import annotations
from types import SimpleNamespace

from jarvis_agent import _record_synthesis


def _make_session():
    s = SimpleNamespace()
    s._jarvis_tts_position_table = []
    return s


class TestRecordSynthesis:
    def test_first_call_initializes_running_totals(self):
        sess = _make_session()
        # 11 chars synthesized into 9600 audio bytes
        # 9600 bytes / 96 (bytes/ms) = 100 ms
        _record_synthesis(sess, input_chars=11, audio_bytes=9600)
        assert sess._jarvis_tts_position_table == [(100, 11)]

    def test_second_call_accumulates(self):
        sess = _make_session()
        _record_synthesis(sess, input_chars=11, audio_bytes=9600)
        _record_synthesis(sess, input_chars=9, audio_bytes=19200)  # 200ms
        assert sess._jarvis_tts_position_table == [(100, 11), (300, 20)]

    def test_session_none_is_noop(self):
        # If the wrapper can't find an active session, recording should
        # silently no-op rather than crash the synthesis pipeline.
        _record_synthesis(None, input_chars=10, audio_bytes=1000)
        # No assertion — just verify no exception.

    def test_table_attr_missing_creates_it(self):
        # Defensive: if the session is missing the attr (e.g., first
        # synthesis before reset block ran), create it.
        sess = SimpleNamespace()  # NO _jarvis_tts_position_table
        _record_synthesis(sess, input_chars=5, audio_bytes=480)  # 5ms
        assert sess._jarvis_tts_position_table == [(5, 5)]

    def test_zero_audio_bytes_records_zero_ms_entry(self):
        # Empty/silent synthesis (e.g., letterless input → silent WAV).
        # Still record the input_chars so the next call's running total
        # is correct (or zero so no false truncation).
        sess = _make_session()
        _record_synthesis(sess, input_chars=3, audio_bytes=0)
        assert sess._jarvis_tts_position_table == [(0, 3)]

    def test_zero_input_chars(self):
        sess = _make_session()
        _record_synthesis(sess, input_chars=0, audio_bytes=9600)
        assert sess._jarvis_tts_position_table == [(100, 0)]
