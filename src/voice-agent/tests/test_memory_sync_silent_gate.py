"""Gates on the cloud memory provider (honcho) sync.

Layer 1 — silent-mode token leak (2026-06-18): a voice-muted JARVIS must
not feed honcho's OpenAI-backed deriver with overheard utterances.

Layer 2 — directed-only sync (2026-07-02): with the reply addressing
gate deliberately OFF (always-answer room), honcho was fed EVERY
overheard utterance; bystander chatter became derived "facts" (the
fabricated "session with Zhaleh — she was watching football" person).
Memory now applies a STRICTER bar than replying: only turns explicitly
addressed to JARVIS (vocative / wake phrase), or within a short
continuation window of one, are synced. Kill-switch:
JARVIS_MEMORY_SYNC_DIRECTED_ONLY=0.

`_should_sync_memory_item(role, text)` is the gate the
`conversation_item_added` handler consults before calling
`memory_provider.sync_item_async`.
"""
from __future__ import annotations

import time

import pytest


@pytest.fixture
def gate(monkeypatch):
    import jarvis_agent
    monkeypatch.setattr(jarvis_agent, "_is_silent", lambda: False)
    monkeypatch.setattr(jarvis_agent, "MEMORY_SYNC_DIRECTED_ONLY", True)
    # stale stamp = no active directed exchange
    monkeypatch.setattr(jarvis_agent, "_last_addressed_interaction", 0.0)
    return jarvis_agent


# ── Layer 2: directed-only ─────────────────────────────────────────


def test_vocative_user_turn_syncs(gate):
    assert gate._should_sync_memory_item("user", "Jarvis, remember my dentist is at 3pm") is True


def test_vocative_touches_the_window(gate):
    gate._should_sync_memory_item("user", "Jarvis, what's the time?")
    assert gate._within_addressed_window() is True


def test_ambient_user_chatter_skipped(gate):
    # THE Zhaleh fix: room chatter with no vocative and no active
    # directed exchange must not become honcho "facts".
    assert gate._should_sync_memory_item("user", "she was watching football all day") is False


def test_ambient_assistant_reply_skipped(gate):
    # JARVIS's always-on replies TO ambient chatter are noise too.
    assert gate._should_sync_memory_item("assistant", "Sounds like a fun match.") is False


def test_followup_within_window_syncs(gate, monkeypatch):
    monkeypatch.setattr(gate, "_last_addressed_interaction", time.monotonic())
    assert gate._should_sync_memory_item("user", "yes, book it for three") is True
    assert gate._should_sync_memory_item("assistant", "Booked for 3pm.") is True


def test_window_expiry_stops_sync(gate, monkeypatch):
    monkeypatch.setattr(
        gate, "_last_addressed_interaction",
        time.monotonic() - gate.MEMORY_SYNC_WINDOW_SEC - 1,
    )
    assert gate._should_sync_memory_item("user", "anyway pass the salt") is False


def test_kill_switch_restores_sync_everything(gate, monkeypatch):
    monkeypatch.setattr(gate, "MEMORY_SYNC_DIRECTED_ONLY", False)
    assert gate._should_sync_memory_item("user", "she was watching football") is True
    assert gate._should_sync_memory_item("assistant", "Noted.") is True


# ── Layer 1: silent mode (unchanged) ───────────────────────────────


def test_skips_when_silent(monkeypatch):
    import jarvis_agent
    monkeypatch.setattr(jarvis_agent, "_is_silent", lambda: True)
    assert jarvis_agent._should_sync_memory_item(
        "user", "Jarvis, overheard ambient chatter"
    ) is False


def test_skips_non_conversation_roles(gate):
    assert gate._should_sync_memory_item("system", "boot prompt") is False
    assert gate._should_sync_memory_item("", "stray") is False


def test_skips_empty_text(gate):
    assert gate._should_sync_memory_item("user", "") is False
    assert gate._should_sync_memory_item("user", "   ") is False
