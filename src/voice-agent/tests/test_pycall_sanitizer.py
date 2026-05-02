"""Tests for pycall_sanitizer — suppresses tool-call-as-Python-text
leaks. Captured live 2026-05-02: Groq llama-3.3-70b emitted
`browser_task_v2(...) task_done(summary)` as content."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import pycall_sanitizer


@pytest.fixture(autouse=True)
def _clean_state():
    pycall_sanitizer._PYCALL_STATE.clear()
    yield
    pycall_sanitizer._PYCALL_STATE.clear()


def test_install_is_idempotent():
    pycall_sanitizer.install()
    pycall_sanitizer.install()
    pycall_sanitizer.install()
    from livekit.agents.inference import llm as inf_llm
    assert getattr(inf_llm.LLMStream, "_jarvis_pycall_patched", False) is True


def _make_self_mock(known_tools):
    return SimpleNamespace(
        _tool_call_id=None, _fnc_name=None, _fnc_raw_arguments=None,
        _tool_extra=None, _tool_index=None,
        _tool_ctx=SimpleNamespace(
            function_tools={name: object() for name in known_tools}
        ),
        _event_ch=SimpleNamespace(send_nowait=lambda c: None),
    )


def _make_choice(content):
    delta = SimpleNamespace(content=content, tool_calls=None, reasoning_content=None)
    return SimpleNamespace(delta=delta, finish_reason=None)


def test_suppresses_pycall_leak_at_start():
    """Captured live: response opens with `browser_task_v2("...")` —
    must be suppressed before TTS hears it."""
    from livekit.agents.inference import llm as inf_llm
    pycall_sanitizer.install()

    self_mock = _make_self_mock({"browser_task_v2", "task_done"})
    import threading
    thinking = threading.Event()

    leak = 'browser_task_v2("go to weather.com and report the current weather")'
    chunks = [
        'browser_task_v2(',
        '"go to weather.com',
        ' and report the current weather"',
        ')',
        '  task_done(summary)',
    ]
    voiced = []
    for content in chunks:
        c = _make_choice(content)
        inf_llm.LLMStream._parse_choice(self_mock, "resp_pycall_1", c, thinking)
        voiced.append(c.delta.content)

    full_voiced = "".join(voiced)
    assert "browser_task_v2" not in full_voiced, (
        f"tool-call leaked to TTS: {full_voiced!r}"
    )
    assert "task_done" not in full_voiced, full_voiced
    assert "weather.com" not in full_voiced, full_voiced


def test_no_op_on_normal_text():
    """A response that begins with normal prose must NOT be touched.
    Common-case guard against false positives."""
    from livekit.agents.inference import llm as inf_llm
    pycall_sanitizer.install()

    self_mock = _make_self_mock({"browser_task_v2", "task_done"})
    import threading
    thinking = threading.Event()

    chunks = [
        "Right ", "away, ", "sir. ", "The ", "weather ",
        "in ", "Columbus ", "is ", "47 degrees.",
    ]
    voiced = []
    for content in chunks:
        c = _make_choice(content)
        inf_llm.LLMStream._parse_choice(self_mock, "resp_pycall_normal", c, thinking)
        voiced.append(c.delta.content)
    assert "".join(voiced) == "".join(chunks), "normal prose was corrupted"


def test_no_trigger_on_unknown_function_name():
    """If the prefix matches `name(` but `name` isn't in the tool
    map, treat as natural English (e.g. someone says 'foo(bar)' as
    plain text). Must NOT trigger suppression."""
    from livekit.agents.inference import llm as inf_llm
    pycall_sanitizer.install()

    self_mock = _make_self_mock({"browser_task_v2", "task_done"})
    import threading
    thinking = threading.Event()

    # `unknown_tool` is NOT in the tool map → don't trigger.
    leak = 'unknown_tool(arg1, arg2) and some text after'
    c = _make_choice(leak)
    inf_llm.LLMStream._parse_choice(self_mock, "resp_pycall_unknown", c, thinking)
    assert c.delta.content == leak, (
        f"falsely suppressed unknown function: {c.delta.content!r}"
    )


def test_state_cleared_when_envelope_balances():
    """When paren depth returns to 0, per-stream state must be
    cleared so subsequent normal text isn't suppressed."""
    from livekit.agents.inference import llm as inf_llm
    pycall_sanitizer.install()

    self_mock = _make_self_mock({"browser_task_v2"})
    import threading
    thinking = threading.Event()

    # Envelope arrives; closes naturally.
    inf_llm.LLMStream._parse_choice(
        self_mock, "resp_clear", _make_choice('browser_task_v2(foo)'), thinking,
    )
    # State should be cleared by now.
    assert "resp_clear" not in pycall_sanitizer._PYCALL_STATE