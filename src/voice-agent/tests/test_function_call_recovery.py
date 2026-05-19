"""L1 — function call recovery helper. Synthesizes a
(FunctionCall, FunctionCallOutput) pair from a parsed text-shaped
tool call and inserts it into chat_ctx so the subagent gate sees
real evidence."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def _fake_chat_ctx():
    """Minimal chat_ctx stand-in — just an `items` list. The real
    LiveKit chat_ctx supports more, but the recovery helper only
    needs .items.append()."""
    return SimpleNamespace(items=[])


def test_synthesize_inserts_pair_with_shared_call_id():
    from sanitizers._function_call_recovery import synthesize_and_insert
    ctx = _fake_chat_ctx()
    fc, fco = synthesize_and_insert(
        chat_ctx=ctx,
        tool_name="launch_app",
        raw_args="binary='google-chrome', args='--new-window'",
        synthetic_output="OK: synthesis_path (call captured from text-shape leak)",
    )
    assert fc.call_id == fco.call_id
    assert fc.name == "launch_app"
    assert "google-chrome" in fc.arguments
    assert "synthesis_path" in fco.output
    # Both items must end up in chat_ctx — gate walks items_since.
    assert len(ctx.items) == 2
    assert ctx.items[0] is fc
    assert ctx.items[1] is fco


def test_synthesize_produces_unique_call_id_per_call():
    from sanitizers._function_call_recovery import synthesize_and_insert
    ctx = _fake_chat_ctx()
    fc1, _ = synthesize_and_insert(
        chat_ctx=ctx, tool_name="launch_app",
        raw_args="binary='a'", synthetic_output="ok",
    )
    fc2, _ = synthesize_and_insert(
        chat_ctx=ctx, tool_name="launch_app",
        raw_args="binary='b'", synthetic_output="ok",
    )
    assert fc1.call_id != fc2.call_id


def test_synthesize_disabled_env_returns_none(monkeypatch):
    """JARVIS_PYCALL_SYNTH_DISABLED=1 → synthesize_and_insert
    short-circuits to None without touching chat_ctx."""
    monkeypatch.setenv("JARVIS_PYCALL_SYNTH_DISABLED", "1")
    from sanitizers._function_call_recovery import synthesize_and_insert
    ctx = _fake_chat_ctx()
    result = synthesize_and_insert(
        chat_ctx=ctx, tool_name="launch_app",
        raw_args="binary='x'", synthetic_output="ok",
    )
    assert result is None
    assert ctx.items == []
