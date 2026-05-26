"""Vocative-armed engagement gate for memory WRITES (2026-05-20
ambient-pollution fix). Cold ambient turns (no recent 'Jarvis')
must not be written to long-term memory."""
import pytest
from pipeline import memory_gate


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.delenv("JARVIS_MEMORY_ENGAGEMENT_GATE", raising=False)
    monkeypatch.delenv("JARVIS_MEMORY_ENGAGEMENT_WINDOW_S", raising=False)
    memory_gate.reset()
    yield
    memory_gate.reset()


def test_cold_turn_no_vocative_is_not_engaged():
    assert memory_gate.is_write_engaged(now=1000.0) is False


def test_armed_within_window_is_engaged():
    memory_gate.note_vocative(now=1000.0)
    assert memory_gate.is_write_engaged(now=1100.0, window_s=180.0) is True


def test_armed_past_window_is_not_engaged():
    memory_gate.note_vocative(now=1000.0)
    assert memory_gate.is_write_engaged(now=1200.0, window_s=180.0) is False


def test_kill_switch_forces_engaged(monkeypatch):
    monkeypatch.setenv("JARVIS_MEMORY_ENGAGEMENT_GATE", "0")
    assert memory_gate.is_write_engaged(now=1000.0) is True  # no vocative needed


def test_window_from_env(monkeypatch):
    monkeypatch.setenv("JARVIS_MEMORY_ENGAGEMENT_WINDOW_S", "60")
    memory_gate.note_vocative(now=1000.0)
    assert memory_gate.is_write_engaged(now=1059.0) is True
    assert memory_gate.is_write_engaged(now=1061.0) is False


def test_revocative_reopens_expired_window():
    memory_gate.note_vocative(now=1000.0)
    assert memory_gate.is_write_engaged(now=1300.0, window_s=180.0) is False  # expired
    memory_gate.note_vocative(now=1300.0)  # re-armed by a fresh vocative
    assert memory_gate.is_write_engaged(now=1400.0, window_s=180.0) is True
