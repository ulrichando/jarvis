"""T14 — verify_launched wired as backup evidence into
has_recent_tool_evidence. Only fires when chat_ctx-only evidence
is weak (no tool_result, only handoffs)."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def _msg(role, content=None, tool_calls=None, tool_name=None):
    return SimpleNamespace(role=role, content=content,
                           tool_calls=tool_calls, name=tool_name)


def test_verify_launched_only_called_when_chat_ctx_evidence_weak(monkeypatch):
    """If chat_ctx already has a real tool_result, verify_launched
    is NOT called (no need)."""
    from confab_detector import has_recent_tool_evidence
    calls = []
    def fake_verify(binary_name, timeout_s=5.0):
        calls.append(binary_name)
        return False
    monkeypatch.setattr("confab_detector.verify_launched", fake_verify)

    items = [
        _msg("user", "open chrome"),
        _msg("assistant", tool_calls=[
            SimpleNamespace(function=SimpleNamespace(name="launch_app"))
        ]),
        _msg("tool", content="OK: launched 'google-chrome'", tool_name="launch_app"),
    ]
    assert has_recent_tool_evidence(items, lookback=10,
                                    verify_launch_for="google-chrome")
    assert calls == []   # verify_launched not called — chat_ctx had evidence


def test_verify_launched_called_when_only_handoff_in_ctx(monkeypatch):
    """When chat_ctx has only a bare transfer_to_* (no tool_result),
    verify_launched is called AND if it returns True, that counts
    as evidence."""
    from confab_detector import has_recent_tool_evidence
    calls = []
    def fake_verify(binary_name, timeout_s=5.0):
        calls.append(binary_name)
        return True   # process IS running
    monkeypatch.setattr("confab_detector.verify_launched", fake_verify)

    items = [
        _msg("user", "open chrome"),
        _msg("assistant", tool_calls=[
            SimpleNamespace(function=SimpleNamespace(name="transfer_to_desktop"))
        ]),
    ]
    assert has_recent_tool_evidence(items, lookback=10,
                                    verify_launch_for="google-chrome")
    assert calls == ["google-chrome"]


def test_verify_launched_returns_false_no_evidence(monkeypatch):
    """If verify_launched returns False (process not running) AND
    chat_ctx has no other evidence, has_recent_tool_evidence
    returns False."""
    from confab_detector import has_recent_tool_evidence
    def fake_verify(binary_name, timeout_s=5.0):
        return False
    monkeypatch.setattr("confab_detector.verify_launched", fake_verify)

    items = [
        _msg("user", "open chrome"),
        _msg("assistant", tool_calls=[
            SimpleNamespace(function=SimpleNamespace(name="transfer_to_desktop"))
        ]),
    ]
    assert not has_recent_tool_evidence(items, lookback=10,
                                        verify_launch_for="google-chrome")


def test_verify_launched_handles_none_return(monkeypatch):
    """verify_launched returns None when pgrep is unavailable.
    Treat as 'unknown' — don't grant evidence credit."""
    from confab_detector import has_recent_tool_evidence
    def fake_verify(binary_name, timeout_s=5.0):
        return None
    monkeypatch.setattr("confab_detector.verify_launched", fake_verify)

    items = [
        _msg("user", "open chrome"),
        _msg("assistant", tool_calls=[
            SimpleNamespace(function=SimpleNamespace(name="transfer_to_desktop"))
        ]),
    ]
    assert not has_recent_tool_evidence(items, lookback=10,
                                        verify_launch_for="google-chrome")


def test_verify_launch_for_none_preserves_existing_behavior():
    """When verify_launch_for is None (default), no pgrep is called.
    Preserves existing strict-rule behavior."""
    from confab_detector import has_recent_tool_evidence
    items = [
        _msg("user", "open chrome"),
        _msg("assistant", tool_calls=[
            SimpleNamespace(function=SimpleNamespace(name="transfer_to_desktop"))
        ]),
    ]
    # No verify_launch_for kwarg → bare handoff doesn't count
    assert not has_recent_tool_evidence(items, lookback=10)
