"""L2 — confab detector stricter evidence rule. transfer_to_*
alone is no longer enough; need a real tool_result or an allowed
(not refused) subagent task_done."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def _msg(role, content=None, tool_calls=None, tool_name=None):
    return SimpleNamespace(role=role, content=content,
                           tool_calls=tool_calls, name=tool_name)


def test_bare_transfer_to_does_not_count_as_evidence():
    """The 2026-05-19 Chrome confab pattern: only a bare
    transfer_to_desktop in the last 10 messages, gate refused the
    subagent's task_done. Detector must NOT grant evidence credit."""
    from confab_detector import has_recent_tool_evidence
    items = [
        _msg(role="user", content="open chrome"),
        _msg(role="assistant", tool_calls=[
            SimpleNamespace(function=SimpleNamespace(name="transfer_to_desktop"))
        ]),
    ]
    assert not has_recent_tool_evidence(items, lookback=10)


def test_real_function_call_output_counts_as_evidence():
    """A structured FunctionCallOutput (or role:tool message)
    counts as evidence — the actual tool ran and returned."""
    from confab_detector import has_recent_tool_evidence
    items = [
        _msg(role="user", content="open chrome"),
        _msg(role="assistant", tool_calls=[
            SimpleNamespace(function=SimpleNamespace(name="launch_app"))
        ]),
        _msg(role="tool", content="OK: launched 'google-chrome'",
             tool_name="launch_app"),
    ]
    assert has_recent_tool_evidence(items, lookback=10)


def test_strict_disabled_env_falls_back_to_permissive(monkeypatch):
    """JARVIS_CONFAB_STRICT_DISABLED=1 reverts to today's rule:
    transfer_to_* alone counts."""
    monkeypatch.setenv("JARVIS_CONFAB_STRICT_DISABLED", "1")
    from confab_detector import has_recent_tool_evidence
    items = [
        _msg(role="user", content="open chrome"),
        _msg(role="assistant", tool_calls=[
            SimpleNamespace(function=SimpleNamespace(name="transfer_to_desktop"))
        ]),
    ]
    assert has_recent_tool_evidence(items, lookback=10)


def test_non_handoff_tool_call_alone_counts_as_evidence():
    """Strict rule: a non-handoff tool_call (e.g., screenshot()) in
    the lookback window counts as evidence even WITHOUT a trailing
    tool_result. The supervisor often invokes its direct tools and
    the result lands on the next turn — we shouldn't refuse the
    immediate reply on the call turn itself."""
    from confab_detector import has_recent_tool_evidence
    items = [
        _msg(role="user", content="what's on screen"),
        _msg(role="assistant", tool_calls=[
            SimpleNamespace(function=SimpleNamespace(name="screenshot"))
        ]),
    ]
    assert has_recent_tool_evidence(items, lookback=10)
