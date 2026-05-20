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
    monkeypatch.setenv("JARVIS_PIPEWIRE_AEC", "1")  # ceiling on, so the source name decides
    monkeypatch.setattr(aec_health, "_default_source_name", lambda: "alsa_input.pci-0000_00_1f.3.analog-stereo")
    aec_health._l1_cache_clear()
    assert aec_health.l1_echo_cancel_active() is False


def test_l1_active_false_when_flag_ceiling_off(monkeypatch):
    from audio import aec_health
    monkeypatch.setattr(aec_health, "_default_source_name", lambda: "echo-cancel-source")
    monkeypatch.setenv("JARVIS_PIPEWIRE_AEC", "0")  # operator ceiling
    aec_health._l1_cache_clear()
    assert aec_health.l1_echo_cancel_active() is False


def test_current_echo_defense_measures_inputs(monkeypatch):
    from audio import aec_health
    monkeypatch.setattr(aec_health, "_default_source_name", lambda: "echo-cancel-source")
    monkeypatch.setenv("JARVIS_PIPEWIRE_AEC", "1")
    aec_health._l1_cache_clear()
    d = aec_health.current_echo_defense(apm_aec=False, dtln_healthy=False)
    assert d.l1 is True and d.l2_aec is False and d.l3 is False


def test_sufficient_denies_on_speakers_by_default(monkeypatch):
    from audio import aec_health
    monkeypatch.setattr(aec_health, "_HOT_MIC_SET", "none")
    d = aec_health.EchoDefense(l1=True, l2_aec=False, l3=True)
    assert aec_health.sufficient_for_hot_mic(d, "speakers") is False


def test_sufficient_headphones_always_true():
    from audio import aec_health
    d = aec_health.EchoDefense(l1=False, l2_aec=False, l3=False)
    assert aec_health.sufficient_for_hot_mic(d, "headphones") is True


def test_sufficient_l1_set(monkeypatch):
    from audio import aec_health
    monkeypatch.setattr(aec_health, "_HOT_MIC_SET", "l1")
    assert aec_health.sufficient_for_hot_mic(aec_health.EchoDefense(True, False, False), "speakers") is True
    assert aec_health.sufficient_for_hot_mic(aec_health.EchoDefense(False, False, True), "speakers") is False


def test_sufficient_l1_l3_set(monkeypatch):
    from audio import aec_health
    monkeypatch.setattr(aec_health, "_HOT_MIC_SET", "l1_l3")
    assert aec_health.sufficient_for_hot_mic(aec_health.EchoDefense(True, False, True), "speakers") is True
    assert aec_health.sufficient_for_hot_mic(aec_health.EchoDefense(True, False, False), "speakers") is False


def test_current_echo_defense_failclosed(monkeypatch):
    from audio import aec_health
    def _boom():
        raise RuntimeError("pw-dump exploded")
    monkeypatch.setattr(aec_health, "l1_echo_cancel_active", _boom)
    d = aec_health.current_echo_defense(apm_aec=False, dtln_healthy=False)
    assert d.l1 is False
