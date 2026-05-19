"""T12 — handoff-refused flag injects a system message into
chat_ctx so the supervisor LLM can see it (and apply the
POST-HANDOFF HONESTY rule from supervisor.md)."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_inject_handoff_refused_marker_when_flag_set():
    """When session._jarvis_last_handoff_refused is True, the
    injector adds a system message containing 'POST-HANDOFF SIGNAL'
    + hedge guidance to chat_ctx.items."""
    from jarvis_agent import inject_handoff_refused_marker
    session = SimpleNamespace(_jarvis_last_handoff_refused=True)
    chat_ctx = SimpleNamespace(items=[])
    inject_handoff_refused_marker(session, chat_ctx)
    assert len(chat_ctx.items) == 1
    msg = chat_ctx.items[0]
    assert getattr(msg, "role", None) == "system"
    content = getattr(msg, "content", None)
    if isinstance(content, list):
        content_text = " ".join(str(c) for c in content)
    else:
        content_text = str(content)
    assert "POST-HANDOFF SIGNAL" in content_text
    assert "REFUSED" in content_text
    assert "hedge" in content_text.lower()


def test_no_injection_when_flag_unset():
    """When the flag is absent or False, nothing is injected."""
    from jarvis_agent import inject_handoff_refused_marker
    session = SimpleNamespace()
    chat_ctx = SimpleNamespace(items=[])
    inject_handoff_refused_marker(session, chat_ctx)
    assert chat_ctx.items == []


def test_inject_clears_the_flag():
    """After injecting, the flag is cleared so the message only
    fires once per gate-refusal event."""
    from jarvis_agent import inject_handoff_refused_marker
    session = SimpleNamespace(_jarvis_last_handoff_refused=True)
    chat_ctx = SimpleNamespace(items=[])
    inject_handoff_refused_marker(session, chat_ctx)
    assert getattr(session, "_jarvis_last_handoff_refused", False) is False


def test_inject_is_idempotent_when_called_twice_after_clear():
    """Calling injector again after the first injection (and clear)
    does NOT add a duplicate system message."""
    from jarvis_agent import inject_handoff_refused_marker
    session = SimpleNamespace(_jarvis_last_handoff_refused=True)
    chat_ctx = SimpleNamespace(items=[])
    inject_handoff_refused_marker(session, chat_ctx)
    assert len(chat_ctx.items) == 1
    # Second call should be a no-op since the flag was cleared.
    inject_handoff_refused_marker(session, chat_ctx)
    assert len(chat_ctx.items) == 1
