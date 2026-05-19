"""L1 integration — pycall sanitizer rescues a text-shaped
launch_app call AND inserts a synthesized FunctionCall +
FunctionCallOutput pair into chat_ctx via the recovery helper.

This is the end-to-end test for the Chrome 02:23:33 failure
pattern."""
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


def _make_self_mock(known_tools, chat_ctx):
    return SimpleNamespace(
        _tool_call_id=None, _fnc_name=None, _fnc_raw_arguments=None,
        _tool_extra=None, _tool_index=None,
        _tool_ctx=SimpleNamespace(
            function_tools={name: object() for name in known_tools}
        ),
        _event_ch=SimpleNamespace(send_nowait=lambda c: None),
        _chat_ctx=chat_ctx,
    )


def _make_choice(content):
    delta = SimpleNamespace(content=content, tool_calls=None, reasoning_content=None)
    return SimpleNamespace(delta=delta, finish_reason=None)


def test_text_shape_launch_app_lands_pair_in_chat_ctx():
    """The 2026-05-19T02:23:33 failure: subagent emits
    launch_app('google-chrome') as text. After fix, pycall
    suppresses the voiced text AND inserts a (FunctionCall,
    FunctionCallOutput) pair into chat_ctx so the gate sees it."""
    from livekit.agents.inference import llm as inf_llm
    pycall_sanitizer.install()

    chat_ctx = SimpleNamespace(items=[])
    self_mock = _make_self_mock({"launch_app", "task_done"}, chat_ctx)
    import threading
    thinking = threading.Event()

    chunks = [
        'launch_app("google-chrome", ',
        '"--profile-directory=Default --new-window")',
    ]
    voiced = []
    for content in chunks:
        c = _make_choice(content)
        inf_llm.LLMStream._parse_choice(self_mock, "resp_synth", c, thinking)
        voiced.append(c.delta.content)

    # Voiced text is suppressed (no TTS gibberish).
    full_voiced = "".join(voiced)
    assert "launch_app" not in full_voiced

    # Pair is in chat_ctx — gate sees items_since=2.
    assert len(chat_ctx.items) == 2
    fc, fco = chat_ctx.items
    assert fc.call_id == fco.call_id
    assert fc.name == "launch_app"
