"""L1 integration — pycall sanitizer rescues a text-shaped
launch_app call AND stashes the parsed call info on the session so
the subagent gate can synthesize a FunctionCall + FunctionCallOutput
pair into its PERSISTED chat_ctx at task_done check time.

This is the end-to-end test for the Chrome 02:23:33 failure pattern.

Updated 2026-05-19 (T13): the synthesis path moved from pycall-time
(which only sees the transient LLMStream._chat_ctx copy) to gate-time
(which has the persisted Agent._chat_ctx). Pycall now STASHES; the
gate DRAINS + synthesizes. See test_session_stash_synthesis.py for
the gate-side drain coverage.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import sanitizers.pycall as pycall_sanitizer


@pytest.fixture(autouse=True)
def _clean_state():
    pycall_sanitizer._PYCALL_STATE.clear()
    yield
    pycall_sanitizer._PYCALL_STATE.clear()


def _make_self_mock(known_tools, chat_ctx, session=None):
    return SimpleNamespace(
        _tool_call_id=None, _fnc_name=None, _fnc_raw_arguments=None,
        _tool_extra=None, _tool_index=None,
        _tool_ctx=SimpleNamespace(
            function_tools={name: object() for name in known_tools}
        ),
        _event_ch=SimpleNamespace(send_nowait=lambda c: None),
        _chat_ctx=chat_ctx,
        _session=session,
    )


def _make_choice(content):
    delta = SimpleNamespace(content=content, tool_calls=None, reasoning_content=None)
    return SimpleNamespace(delta=delta, finish_reason=None)


def test_text_shape_launch_app_stashes_call_on_session():
    """The 2026-05-19T02:23:33 failure: subagent emits
    launch_app('google-chrome') as text. After T13 fix, pycall
    suppresses the voiced text AND stashes the parsed call info on
    session._jarvis_text_shape_pending so the gate can synthesize a
    (FunctionCall, FunctionCallOutput) pair into the persisted
    chat_ctx at task_done check time."""
    from livekit.agents.inference import llm as inf_llm
    pycall_sanitizer.install()

    chat_ctx = SimpleNamespace(items=[])
    session = SimpleNamespace()
    self_mock = _make_self_mock({"launch_app", "task_done"}, chat_ctx, session=session)
    import threading
    thinking = threading.Event()

    # Single-chunk form with balanced parens so the args slice
    # extracts on this first chunk (best-effort; pycall doesn't
    # accumulate args across chunks — multi-chunk leaks land with
    # empty raw_args, which the gate's synthesize_and_insert handles
    # gracefully). The 02:23:33 production capture was multi-chunk
    # and the args were lost there too — they're not load-bearing
    # for the gate's no-tool refusal recovery.
    chunks = [
        'launch_app("google-chrome", "--profile-directory=Default --new-window")',
    ]
    voiced = []
    for content in chunks:
        c = _make_choice(content)
        inf_llm.LLMStream._parse_choice(self_mock, "resp_synth", c, thinking)
        voiced.append(c.delta.content)

    # Voiced text is suppressed (no TTS gibberish).
    full_voiced = "".join(voiced)
    assert "launch_app" not in full_voiced

    # Pycall no longer writes into chat_ctx directly — that path
    # moved to the subagent gate. Confirm chat_ctx is untouched here.
    assert chat_ctx.items == []

    # Instead, pycall stashes the parsed call on the session. The
    # gate's _drain_text_shape_stash picks this up at task_done check
    # time and lands the (FunctionCall, FunctionCallOutput) pair in
    # the persisted Agent._chat_ctx.
    pending = getattr(session, "_jarvis_text_shape_pending", None)
    assert pending is not None
    assert len(pending) == 1
    assert pending[0]["tool_name"] == "launch_app"
    assert "google-chrome" in pending[0]["raw_args"]
