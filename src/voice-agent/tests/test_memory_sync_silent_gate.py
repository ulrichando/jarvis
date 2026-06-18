"""Silent-mode token-leak fix (2026-06-18): the cloud memory provider
(honcho) must NOT be fed conversation items while JARVIS is silenced.

Otherwise a voice-muted JARVIS keeps streaming every overheard utterance
into honcho's OpenAI-backed deriver, burning tokens the user can't hear —
the leak the user found ("on mute he still listens, we just can't hear
the output").

Spec: docs/superpowers/specs/2026-06-18-silent-mode-token-leak-fix-design.md

`_should_sync_memory_item(role, text)` is the pure gate the
`conversation_item_added` handler consults before calling
`memory_provider.sync_item_async`.
"""
from __future__ import annotations


def test_syncs_user_message_when_active(monkeypatch):
    import jarvis_agent
    monkeypatch.setattr(jarvis_agent, "_is_silent", lambda: False)
    assert jarvis_agent._should_sync_memory_item(
        "user", "remember my dentist is at 3pm"
    ) is True


def test_syncs_assistant_message_when_active(monkeypatch):
    import jarvis_agent
    monkeypatch.setattr(jarvis_agent, "_is_silent", lambda: False)
    assert jarvis_agent._should_sync_memory_item("assistant", "Noted.") is True


def test_skips_when_silent(monkeypatch):
    # THE leak fix: a silenced JARVIS must not feed honcho.
    import jarvis_agent
    monkeypatch.setattr(jarvis_agent, "_is_silent", lambda: True)
    assert jarvis_agent._should_sync_memory_item(
        "user", "overheard ambient chatter"
    ) is False


def test_skips_non_conversation_roles(monkeypatch):
    import jarvis_agent
    monkeypatch.setattr(jarvis_agent, "_is_silent", lambda: False)
    assert jarvis_agent._should_sync_memory_item("system", "boot prompt") is False
    assert jarvis_agent._should_sync_memory_item("", "stray") is False


def test_skips_empty_text(monkeypatch):
    import jarvis_agent
    monkeypatch.setattr(jarvis_agent, "_is_silent", lambda: False)
    assert jarvis_agent._should_sync_memory_item("user", "") is False
    assert jarvis_agent._should_sync_memory_item("user", "   ") is False
