"""Tests for the AEC health probe + gate predicate (2026-05-20)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_l1_active_true_when_default_source_is_echo_cancel(monkeypatch):
    from audio import aec_health
    monkeypatch.setattr(aec_health, "_default_source_name", lambda: "echo-cancel-source")
    monkeypatch.setenv("JARVIS_PIPEWIRE_AEC", "1")
    aec_health._l1_cache_clear()
    assert aec_health.l1_echo_cancel_active() is True


def test_l1_active_false_when_default_source_is_raw_mic(monkeypatch):
    from audio import aec_health
    monkeypatch.setattr(aec_health, "_default_source_name", lambda: "alsa_input.pci-0000_00_1f.3.analog-stereo")
    aec_health._l1_cache_clear()
    assert aec_health.l1_echo_cancel_active() is False


def test_l1_active_false_when_flag_ceiling_off(monkeypatch):
    from audio import aec_health
    monkeypatch.setattr(aec_health, "_default_source_name", lambda: "echo-cancel-source")
    monkeypatch.setenv("JARVIS_PIPEWIRE_AEC", "0")  # operator ceiling
    aec_health._l1_cache_clear()
    assert aec_health.l1_echo_cancel_active() is False
