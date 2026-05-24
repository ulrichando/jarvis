"""Track 1 — system-message inject through _maybe_inject_trigger_message helper."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

_VOICE_AGENT_ROOT = Path(__file__).resolve().parent.parent
if str(_VOICE_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_VOICE_AGENT_ROOT))


def test_inject_save_in_live_mode(monkeypatch):
    monkeypatch.setenv("JARVIS_SAVE_TRIGGER_LIVE", "1")
    from jarvis_agent import _maybe_inject_trigger_message
    chat_ctx = MagicMock()
    chat_ctx.add_message = MagicMock()
    fired = _maybe_inject_trigger_message(chat_ctx, "Jarvis, remember this: I love fish")
    assert fired == "save"
    chat_ctx.add_message.assert_called_once()
    # Check the injected message mentions save instruction
    call = chat_ctx.add_message.call_args
    args_str = repr(call)
    assert "USER REQUESTED A SAVE" in args_str


def test_no_inject_save_in_shadow_mode(monkeypatch):
    monkeypatch.delenv("JARVIS_SAVE_TRIGGER_LIVE", raising=False)
    from jarvis_agent import _maybe_inject_trigger_message
    chat_ctx = MagicMock()
    chat_ctx.add_message = MagicMock()
    fired = _maybe_inject_trigger_message(chat_ctx, "Jarvis remember this: fish")
    # Shadow mode: returns "save_shadow" but does NOT call add_message
    assert fired == "save_shadow"
    chat_ctx.add_message.assert_not_called()


def test_inject_recall_in_live_mode(monkeypatch):
    monkeypatch.setenv("JARVIS_RECALL_TRIGGER_LIVE", "1")
    from jarvis_agent import _maybe_inject_trigger_message
    chat_ctx = MagicMock()
    chat_ctx.add_message = MagicMock()
    fired = _maybe_inject_trigger_message(chat_ctx, "do you remember Shelby?")
    assert fired == "recall"
    chat_ctx.add_message.assert_called_once()


def test_no_inject_when_no_match(monkeypatch):
    monkeypatch.setenv("JARVIS_SAVE_TRIGGER_LIVE", "1")
    monkeypatch.setenv("JARVIS_RECALL_TRIGGER_LIVE", "1")
    from jarvis_agent import _maybe_inject_trigger_message
    chat_ctx = MagicMock()
    chat_ctx.add_message = MagicMock()
    fired = _maybe_inject_trigger_message(chat_ctx, "what's the weather")
    assert fired is None
    chat_ctx.add_message.assert_not_called()


def test_empty_text_returns_none(monkeypatch):
    monkeypatch.setenv("JARVIS_SAVE_TRIGGER_LIVE", "1")
    from jarvis_agent import _maybe_inject_trigger_message
    chat_ctx = MagicMock()
    chat_ctx.add_message = MagicMock()
    assert _maybe_inject_trigger_message(chat_ctx, "") is None
    assert _maybe_inject_trigger_message(chat_ctx, "   ") is None
    chat_ctx.add_message.assert_not_called()
