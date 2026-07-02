"""Tests for the runtime voice-style store + supervisor tool.

Feature 2026-07-02: "Jarvis, speak slower" must be a real knob, not a
claim. Live gap: the user asked JARVIS to count slowly / speak slower
and it replied "Got it — I'll speak slower" with no mechanism. Speed is
a flat file read PER SYNTHESIS by both TTS engines (Kokoro `speed`
0.25–4.0, EdgeTTS `rate` percent string), so changes land on the next
utterance without a restart.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipeline import voice_style


@pytest.fixture
def style_files(tmp_path, monkeypatch):
    monkeypatch.setattr(voice_style, "SPEED_FILE", tmp_path / "tts-speed")
    monkeypatch.setattr(voice_style, "PITCH_FILE", tmp_path / "tts-pitch")
    return tmp_path


class TestSpeedStore:
    def test_default_when_unset(self, style_files):
        assert voice_style.get_speed() == 1.0
        assert voice_style.get_speed(default=0.9) == 0.9

    def test_roundtrip(self, style_files):
        assert voice_style.set_speed(0.8) == 0.8
        assert voice_style.get_speed() == 0.8

    def test_clamped_low_and_high(self, style_files):
        assert voice_style.set_speed(0.1) == voice_style.SPEED_MIN
        assert voice_style.set_speed(5.0) == voice_style.SPEED_MAX

    def test_garbage_file_falls_back(self, style_files):
        voice_style.SPEED_FILE.write_text("not-a-number\n")
        assert voice_style.get_speed() == 1.0

    def test_reset_removes_files(self, style_files):
        voice_style.set_speed(0.8)
        voice_style.set_pitch_hz(5)
        voice_style.reset()
        assert voice_style.get_speed() == 1.0
        assert voice_style.get_pitch_hz() == 0


class TestPitchStore:
    def test_roundtrip_and_clamp(self, style_files):
        assert voice_style.set_pitch_hz(5) == 5
        assert voice_style.get_pitch_hz() == 5
        assert voice_style.set_pitch_hz(999) == voice_style.PITCH_MAX_HZ
        assert voice_style.set_pitch_hz(-999) == voice_style.PITCH_MIN_HZ


class TestEdgeMapping:
    def test_rate_string_from_speed(self, style_files):
        voice_style.set_speed(0.8)
        assert voice_style.edge_rate_string("+0%") == "-20%"
        voice_style.set_speed(1.25)
        assert voice_style.edge_rate_string("+0%") == "+25%"

    def test_rate_fallback_when_unset(self, style_files):
        assert voice_style.edge_rate_string("+3%") == "+3%"

    def test_pitch_string(self, style_files):
        voice_style.set_pitch_hz(-4)
        assert voice_style.edge_pitch_string("+0Hz") == "-4Hz"
        voice_style.reset()
        assert voice_style.edge_pitch_string("+2Hz") == "+2Hz"


class TestVoiceStyleTool:
    def _call(self, **args):
        from tools.voice_style import _handle_voice_style
        import json
        return json.loads(_handle_voice_style(args))

    def test_get_reports_current(self, style_files):
        out = self._call(action="get")
        assert out["speed"] == 1.0

    def test_absolute_set(self, style_files):
        out = self._call(action="set", speed=0.8)
        assert out["speed"] == 0.8
        assert voice_style.get_speed() == 0.8

    def test_word_slower_steps_down(self, style_files):
        voice_style.set_speed(1.0)
        out = self._call(action="set", speed="slower")
        assert out["speed"] == 0.9
        out = self._call(action="set", speed="slower")
        assert out["speed"] == 0.8

    def test_word_faster_steps_up(self, style_files):
        voice_style.set_speed(1.0)
        out = self._call(action="set", speed="faster")
        assert out["speed"] == 1.1

    def test_word_presets(self, style_files):
        assert self._call(action="set", speed="slow")["speed"] == 0.8
        assert self._call(action="set", speed="normal")["speed"] == 1.0
        assert self._call(action="set", speed="fast")["speed"] == 1.2

    def test_set_clamps_and_says_so(self, style_files):
        out = self._call(action="set", speed=9)
        assert out["speed"] == voice_style.SPEED_MAX

    def test_reset(self, style_files):
        voice_style.set_speed(0.7)
        out = self._call(action="reset")
        assert out["speed"] == 1.0
        assert voice_style.get_speed() == 1.0

    def test_pitch_set(self, style_files):
        out = self._call(action="set", pitch_hz=-5)
        assert out["pitch_hz"] == -5

    def test_bad_word_errors(self, style_files):
        out = self._call(action="set", speed="warp9")
        assert "error" in str(out).lower() or "ERROR" in str(out)
