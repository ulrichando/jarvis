"""Tests for dsml_sanitizer — the patch that suppresses DeepSeek's
DSML tool-call envelope from streaming to TTS and inline-executes the
recovered tool call.

Failure captured live 2026-05-01: weather query → DeepSeek emitted
`<｜｜DSML｜｜tool_calls>... <｜｜DSML｜｜invoke name="web_fetch">...
<｜｜DSML｜｜parameter name="url" string="true">https://...</...>` as
plain text. JARVIS read the URL out loud. These tests lock the
suppression + recovery behavior so that doesn't recur.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import sanitizers.dsml as dsml_sanitizer


# ── Pure-function envelope parser ────────────────────────────────────


def test_parse_envelope_extracts_name_and_args():
    envelope = (
        "<｜｜DSML｜｜tool_calls>\n"
        '<｜｜DSML｜｜invoke name="web_fetch">\n'
        '<｜｜DSML｜｜parameter name="url" string="true">'
        "https://example.com</｜｜DSML｜｜parameter>\n"
        "</｜｜DSML｜｜invoke>\n"
        "</｜｜DSML｜｜tool_calls>"
    )
    invokes = dsml_sanitizer._parse_envelope(envelope)
    assert invokes == [("web_fetch", {"url": "https://example.com"})]


def test_parse_envelope_handles_multiple_invokes():
    envelope = (
        "<｜｜DSML｜｜tool_calls>"
        '<｜｜DSML｜｜invoke name="a">'
        '<｜｜DSML｜｜parameter name="x">1</｜｜DSML｜｜parameter>'
        "</｜｜DSML｜｜invoke>"
        '<｜｜DSML｜｜invoke name="b">'
        '<｜｜DSML｜｜parameter name="y">2</｜｜DSML｜｜parameter>'
        "</｜｜DSML｜｜invoke>"
        "</｜｜DSML｜｜tool_calls>"
    )
    invokes = dsml_sanitizer._parse_envelope(envelope)
    assert len(invokes) == 2
    assert invokes[0] == ("a", {"x": "1"})
    assert invokes[1] == ("b", {"y": "2"})


def test_parse_envelope_handles_multi_arg_invoke():
    envelope = (
        "<｜｜DSML｜｜tool_calls>"
        '<｜｜DSML｜｜invoke name="bash">'
        '<｜｜DSML｜｜parameter name="command">ls -la</｜｜DSML｜｜parameter>'
        '<｜｜DSML｜｜parameter name="timeout_ms">5000</｜｜DSML｜｜parameter>'
        "</｜｜DSML｜｜invoke>"
        "</｜｜DSML｜｜tool_calls>"
    )
    invokes = dsml_sanitizer._parse_envelope(envelope)
    assert invokes == [("bash", {"command": "ls -la", "timeout_ms": "5000"})]


def test_parse_envelope_returns_empty_for_garbage():
    assert dsml_sanitizer._parse_envelope("") == []
    assert dsml_sanitizer._parse_envelope("just plain text") == []


# ── Idempotent install ───────────────────────────────────────────────


def test_install_is_idempotent():
    """Calling install() multiple times must not double-wrap."""
    dsml_sanitizer.install()
    dsml_sanitizer.install()
    dsml_sanitizer.install()
    from livekit.agents.inference import llm as inf_llm
    assert getattr(inf_llm.LLMStream, "_jarvis_dsml_patched", False) is True


# ── Streaming integration: suppression + buffering ───────────────────


@pytest.fixture(autouse=True)
def _clean_state():
    dsml_sanitizer._DSML_STATE.clear()
    yield
    dsml_sanitizer._DSML_STATE.clear()


def test_parse_choice_swallows_dsml_envelope():
    """Stream chunk-by-chunk through _parse_choice and assert the DSML
    text gets stripped from delta.content while normal text passes
    through unchanged. The detector triggers on a single U+FF5C
    (｜) character, so split-across-chunks openers are caught."""
    from livekit.agents.inference import llm as inf_llm
    dsml_sanitizer.install()

    response_id = "resp_dsml_1"

    # Build minimal mock self with the attrs the patches read.
    self_mock = SimpleNamespace(
        _tool_call_id=None, _fnc_name=None, _fnc_raw_arguments=None,
        _tool_extra=None, _tool_index=None,
        _tool_ctx=SimpleNamespace(function_tools={}),
        _event_ch=SimpleNamespace(send_nowait=lambda c: None),
    )
    import threading
    thinking = threading.Event()

    def _make_choice(content=None, finish=None):
        delta = SimpleNamespace(
            content=content, tool_calls=None, reasoning_content=None,
        )
        return SimpleNamespace(delta=delta, finish_reason=finish)

    # Chunk 1: pre-text, normal — should pass through.
    c1 = _make_choice(content="Sure, sir. ")
    inf_llm.LLMStream._parse_choice(self_mock, response_id, c1, thinking)
    assert c1.delta.content == "Sure, sir. "

    # Chunk 2: opener arrives with some inline args — should swallow envelope, leave pre-text.
    c2 = _make_choice(content='Looking it up. <｜｜DSML｜｜tool_calls>\n<｜｜DSML｜｜invoke name="x">')
    inf_llm.LLMStream._parse_choice(self_mock, response_id, c2, thinking)
    assert c2.delta.content == "Looking it up. ", "pre-DSML text should pass through"

    # Chunk 3-5: middle of envelope — all swallowed (delta.content set to "")
    for content in [
        '<｜｜DSML｜｜parameter name="q">',
        "hello",
        '</｜｜DSML｜｜parameter>',
    ]:
        c = _make_choice(content=content)
        inf_llm.LLMStream._parse_choice(self_mock, response_id, c, thinking)
        assert c.delta.content == "", f"inside envelope, content was {c.delta.content!r}"

    # Chunk 6: closer — also swallowed; envelope buffer flushed.
    c6 = _make_choice(content='</｜｜DSML｜｜invoke>\n</｜｜DSML｜｜tool_calls>')
    inf_llm.LLMStream._parse_choice(self_mock, response_id, c6, thinking)
    assert c6.delta.content == ""
    # State cleared after closer.
    assert response_id not in dsml_sanitizer._DSML_STATE


def test_parse_choice_handles_split_dsml_opener():
    """The 22-char DSML opener can arrive split across many chunks
    because each `｜` character is its own DeepSeek token. The
    detector must trigger on the FIRST chunk containing any U+FF5C
    char — not require the full opener in one chunk. Captured live
    2026-05-02: 'At once, sir. <｜｜DSML｜｜tool_calls>...' leaked
    because the previous detector waited for the full opener."""
    from livekit.agents.inference import llm as inf_llm
    dsml_sanitizer.install()

    response_id = "resp_split"
    self_mock = SimpleNamespace(
        _tool_call_id=None, _fnc_name=None, _fnc_raw_arguments=None,
        _tool_extra=None, _tool_index=None,
        _tool_ctx=SimpleNamespace(function_tools={}),
        _event_ch=SimpleNamespace(send_nowait=lambda c: None),
    )
    import threading
    thinking = threading.Event()

    def _make(content):
        delta = SimpleNamespace(content=content, tool_calls=None, reasoning_content=None)
        return SimpleNamespace(delta=delta, finish_reason=None)

    # Stream the opener split across 5 chunks the way DeepSeek tokenizes
    # it. Each chunk individually does NOT contain the full
    # "<｜｜DSML｜｜tool_calls>" string, so the OLD detector silently
    # missed it. The new trigger-char detector catches the first `｜`.
    chunks_in = [
        "At once, sir. <",   # pre-text + envelope start
        "｜",                 # ← first trigger char; should switch to swallow
        "｜DSML",
        "｜｜tool_calls>",
        '<｜｜DSML｜｜invoke name="open_app">',
        '<｜｜DSML｜｜parameter name="x">y</｜｜DSML｜｜parameter>',
        "</｜｜DSML｜｜invoke>",
        "</｜｜DSML｜｜tool_calls>",
    ]
    voiced = []
    for content in chunks_in:
        c = _make(content)
        inf_llm.LLMStream._parse_choice(self_mock, response_id, c, thinking)
        voiced.append(c.delta.content)
    # Only the pre-text "At once, sir. " should have reached TTS.
    full_voiced = "".join(voiced)
    assert "At once, sir." in full_voiced, "pre-text dropped"
    assert "｜" not in full_voiced, f"DSML char leaked to TTS: {full_voiced!r}"
    assert "DSML" not in full_voiced, f"DSML markup leaked: {full_voiced!r}"
    assert "tool_calls" not in full_voiced, f"tool_calls leaked: {full_voiced!r}"


def test_parse_choice_no_op_on_normal_text():
    """When NO DSML markers in the stream, every chunk passes through
    unchanged. This is the common-case guard — we must not corrupt
    Groq / OpenAI output."""
    from livekit.agents.inference import llm as inf_llm
    dsml_sanitizer.install()

    response_id = "resp_normal"
    self_mock = SimpleNamespace(
        _tool_call_id=None, _fnc_name=None, _fnc_raw_arguments=None,
        _tool_extra=None, _tool_index=None,
        _tool_ctx=SimpleNamespace(function_tools={}),
        _event_ch=SimpleNamespace(send_nowait=lambda c: None),
    )
    import threading
    thinking = threading.Event()

    chunks_text = [
        "Right ", "away, ", "sir. ", "The ", "weather ", "in ", "Columbus ",
        "is ", "65 degrees ", "and ", "sunny.",
    ]
    for text in chunks_text:
        delta = SimpleNamespace(content=text, tool_calls=None, reasoning_content=None)
        choice = SimpleNamespace(delta=delta, finish_reason=None)
        inf_llm.LLMStream._parse_choice(self_mock, response_id, choice, thinking)
        assert choice.delta.content == text, f"non-DSML chunk corrupted: {choice.delta.content!r}"

    # Buffer should not have been touched.
    assert response_id not in dsml_sanitizer._DSML_STATE
