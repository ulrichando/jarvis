"""T11 — confab_check_state computed and passed to log_turn.

Verifies the 5-way enum compute logic.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def _msg(role, content=None, tool_calls=None, tool_name=None):
    return SimpleNamespace(role=role, content=content,
                           tool_calls=tool_calls, name=tool_name)


def test_refused_handoff_takes_priority():
    """When session._jarvis_last_handoff_refused==True, state is
    'refused_handoff' even if other signals are present."""
    from jarvis_agent import compute_confab_check_state
    session = SimpleNamespace(_jarvis_last_handoff_refused=True)
    chat_items = [
        _msg("user", "open chrome"),
        _msg("assistant", tool_calls=[
            SimpleNamespace(function=SimpleNamespace(name="transfer_to_desktop"))
        ]),
    ]
    state = compute_confab_check_state(
        session=session, chat_items=chat_items, jarvis_text="Done."
    )
    assert state == "refused_handoff"


def test_evidence_ok_on_real_tool_result():
    """A structured tool_result in chat_ctx yields evidence_ok."""
    from jarvis_agent import compute_confab_check_state
    session = SimpleNamespace()
    chat_items = [
        _msg("user", "open chrome"),
        _msg("assistant", tool_calls=[
            SimpleNamespace(function=SimpleNamespace(name="launch_app"))
        ]),
        _msg("tool", content="OK: launched 'google-chrome'", tool_name="launch_app"),
    ]
    state = compute_confab_check_state(
        session=session, chat_items=chat_items, jarvis_text="Chrome's open."
    )
    assert state == "evidence_ok"


def test_hedged_no_evidence_on_hedge_phrasing():
    """When jarvis_text contains hedge phrases AND no evidence,
    state is 'hedged_no_evidence'."""
    from jarvis_agent import compute_confab_check_state
    session = SimpleNamespace()
    chat_items = [
        _msg("user", "open chrome"),
        _msg("assistant", tool_calls=[
            SimpleNamespace(function=SimpleNamespace(name="transfer_to_desktop"))
        ]),
    ]
    state = compute_confab_check_state(
        session=session, chat_items=chat_items,
        jarvis_text="I tried but couldn't confirm — want me to check?",
    )
    assert state == "hedged_no_evidence"


def test_unchecked_when_no_signals():
    """BANTER turns with no handoff, no evidence, no hedge → unchecked."""
    from jarvis_agent import compute_confab_check_state
    session = SimpleNamespace()
    chat_items = [
        _msg("user", "hi"),
    ]
    state = compute_confab_check_state(
        session=session, chat_items=chat_items, jarvis_text="Hello."
    )
    assert state == "unchecked"
