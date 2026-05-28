"""Tests for pycall_sanitizer — suppresses tool-call-as-Python-text
leaks. Captured live 2026-05-02: Groq llama-3.3-70b emitted
`browser_task_v2(...) task_done(summary)` as content."""
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


def test_suppresses_dotted_pycall_leak():
    """2026-05-18 live capture: supervisor LLM emitted
    `computer.screenshot()` as voiced text (a single chunk, after
    narrating its intent). The pycall regex was bare-name-only so the
    dotted method form leaked through to TTS. The user heard literal
    'computer dot screenshot open paren close paren' — robotic
    sounding. Capture: ~/.local/share/jarvis/logs/voice-agent.log
    2026-05-18T15:36:10."""
    from livekit.agents.inference import llm as inf_llm
    pycall_sanitizer.install()

    # `screenshot` IS a live supervisor tool (jarvis_agent.py).
    self_mock = _make_self_mock({"screenshot", "task_done"})
    import threading
    thinking = threading.Event()

    leak = "computer.screenshot()"
    c = _make_choice(leak)
    inf_llm.LLMStream._parse_choice(self_mock, "resp_dotted_1", c, thinking)
    assert "computer" not in c.delta.content, (
        f"dotted tool-call leaked to TTS: {c.delta.content!r}"
    )
    assert "screenshot" not in c.delta.content, c.delta.content


def test_dotted_pycall_unknown_tail_not_suppressed():
    """`John.Smith(` in natural prose — `Smith` is NOT a tool. Must
    NOT trigger suppression. Belt-and-suspenders against the dotted
    pattern being too aggressive."""
    from livekit.agents.inference import llm as inf_llm
    pycall_sanitizer.install()

    self_mock = _make_self_mock({"screenshot", "task_done"})
    import threading
    thinking = threading.Event()

    natural = "I went to John.Smith(university) yesterday."
    c = _make_choice(natural)
    inf_llm.LLMStream._parse_choice(self_mock, "resp_dotted_2", c, thinking)
    assert c.delta.content == natural, (
        f"falsely suppressed natural prose: {c.delta.content!r}"
    )


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

# ── F-arch-011 / W-015: XML-attribute form + subagent-tool leaks ─────


def test_suppresses_xml_attribute_form():
    """Live-captured 2026-05-05 22:06: llama-3.1-8b-instant emitted
    `<function=ext_screenshot>null</function>` as plain content.
    The angle-bracket envelope is unambiguously a tool-call leak;
    must be suppressed even when the tool isn't in the live tool_ctx
    (the BANTER LLM doesn't have ext_screenshot)."""
    from livekit.agents.inference import llm as inf_llm
    pycall_sanitizer.install()

    self_mock = _make_self_mock(set())  # No tools registered locally.
    import threading
    thinking = threading.Event()

    chunks = ["<function=ext_screenshot>", "null", "</function>"]
    voiced = []
    for content in chunks:
        c = _make_choice(content)
        inf_llm.LLMStream._parse_choice(self_mock, "resp_xml_1", c, thinking)
        voiced.append(c.delta.content)

    full = "".join(voiced)
    assert "ext_screenshot" not in full, (
        f"XML-form leak reached TTS: {full!r}"
    )
    assert "<function" not in full, full
    assert "</function>" not in full, full


def test_suppresses_subagent_tool_leak_from_non_local_llm():
    """Live-captured 2026-05-05 22:07: supervisor LLM emitted
    `task_done("user changed topic")` as plain content. `task_done`
    is a per-subagent auto-attached tool — never in the supervisor
    LLM's tool_ctx. The original guard skipped because of that.

    Must be suppressed: no LLM should EVER speak `task_done(...)` to
    the user; it's an internal handoff signal.
    """
    from livekit.agents.inference import llm as inf_llm
    pycall_sanitizer.install()

    # Supervisor's tool_ctx (deliberately doesn't contain task_done).
    self_mock = _make_self_mock({"transfer_to_browser", "transfer_to_planner"})
    import threading
    thinking = threading.Event()

    leak = 'task_done("user changed topic") '
    c = _make_choice(leak)
    inf_llm.LLMStream._parse_choice(self_mock, "resp_subagent_1", c, thinking)
    assert "task_done" not in (c.delta.content or ""), (
        f"subagent-internal tool leaked: {c.delta.content!r}"
    )


def test_suppresses_ext_prefix_tool_leak():
    """Browser subagent tools all have the `ext_*` prefix. The XML
    form already covers `<function=ext_X>` but the Python form
    `ext_navigate(url)` should also be suppressed even when the LLM
    that's currently active doesn't have ext_navigate registered
    (e.g., a BANTER fast-path turn that mistakenly emits a browser
    tool name)."""
    from livekit.agents.inference import llm as inf_llm
    pycall_sanitizer.install()

    self_mock = _make_self_mock(set())  # No tools registered locally.
    import threading
    thinking = threading.Event()

    leak = 'ext_navigate("https://example.com")'
    c = _make_choice(leak)
    inf_llm.LLMStream._parse_choice(self_mock, "resp_ext_1", c, thinking)
    assert "ext_navigate" not in (c.delta.content or ""), (
        f"ext_* prefix leak reached TTS: {c.delta.content!r}"
    )


def test_random_function_call_text_still_passes_through():
    """Negative test: plain text that happens to look like a function
    call (e.g. user said "show me how to write a print statement: "
    print(hello)" must NOT be suppressed if `print` isn't a JARVIS
    tool. The known-leak gate still protects against false positives.
    """
    from livekit.agents.inference import llm as inf_llm
    pycall_sanitizer.install()

    self_mock = _make_self_mock(set())
    import threading
    thinking = threading.Event()

    text = 'print(hello world)'
    c = _make_choice(text)
    inf_llm.LLMStream._parse_choice(self_mock, "resp_negative_1", c, thinking)
    assert c.delta.content == text, (
        f"false positive: legitimate text suppressed: {c.delta.content!r}"
    )


def test_xml_envelope_state_clears_after_close():
    """XML form must clear per-stream state when </function> appears."""
    from livekit.agents.inference import llm as inf_llm
    pycall_sanitizer.install()

    self_mock = _make_self_mock(set())
    import threading
    thinking = threading.Event()

    chunks = ["<function=ext_navigate>", "null</function>"]
    for content in chunks:
        c = _make_choice(content)
        inf_llm.LLMStream._parse_choice(self_mock, "resp_xml_clear", c, thinking)

    assert "resp_xml_clear" not in pycall_sanitizer._PYCALL_STATE, (
        "XML envelope state not cleared after close — subsequent normal "
        "text on a different stream id is unaffected, but for this stream "
        "it'd suppress legitimate content."
    )


# ── F-arch-012 / W-016: 3-tag XML form + JSON array form ─────────────


def test_suppresses_xml_bare_tag_form():
    """Live-captured 2026-05-05 22:20 turn 944: `<function>task_done
    </function><arguments>"Searched for doctors"</arguments>`. The 3-tag
    form (open <function>, name as text, close </function>, then
    <arguments>...</arguments>). Must suppress fully."""
    from livekit.agents.inference import llm as inf_llm
    pycall_sanitizer.install()

    self_mock = _make_self_mock(set())
    import threading
    thinking = threading.Event()

    chunks = [
        "<function>",
        "task_done",
        "</function>",
        '<arguments>"Searched for doctors"</arguments>',
    ]
    voiced = []
    for content in chunks:
        c = _make_choice(content)
        inf_llm.LLMStream._parse_choice(self_mock, "resp_xml_bare_1", c, thinking)
        voiced.append(c.delta.content or "")

    full = "".join(voiced)
    assert "<function>" not in full, f"3-tag leak reached TTS: {full!r}"
    assert "task_done" not in full, full
    assert "<arguments>" not in full, full
    assert "Searched for doctors" not in full, full


def test_suppresses_xml_bare_tag_form_no_arguments_block():
    """Variant: `<function>name</function>` with NO trailing
    <arguments> block. Must close on the lone </function>."""
    from livekit.agents.inference import llm as inf_llm
    pycall_sanitizer.install()

    self_mock = _make_self_mock(set())
    import threading
    thinking = threading.Event()

    chunks = ["<function>", "ext_click", "</function>"]
    voiced = []
    for content in chunks:
        c = _make_choice(content)
        inf_llm.LLMStream._parse_choice(self_mock, "resp_xml_bare_2", c, thinking)
        voiced.append(c.delta.content or "")

    full = "".join(voiced)
    assert "<function>" not in full
    assert "ext_click" not in full
    assert "resp_xml_bare_2" not in pycall_sanitizer._PYCALL_STATE


def test_suppresses_json_tool_array_form():
    """Live-captured 2026-05-05 22:16 turn 930: outright JSON array
    of tool-call objects bypassing the tool-call protocol entirely."""
    from livekit.agents.inference import llm as inf_llm
    pycall_sanitizer.install()

    self_mock = _make_self_mock(set())
    import threading
    thinking = threading.Event()

    chunks = [
        '[\n  {\n    "name": "ext_dom_summary",',
        '\n    "parameters": {}\n  }',
        '\n]',
    ]
    voiced = []
    for content in chunks:
        c = _make_choice(content)
        inf_llm.LLMStream._parse_choice(self_mock, "resp_json_1", c, thinking)
        voiced.append(c.delta.content or "")

    full = "".join(voiced)
    assert "ext_dom_summary" not in full, f"JSON array leak reached TTS: {full!r}"
    assert "parameters" not in full
    assert '"name"' not in full


def test_random_json_array_passes_through():
    """Negative test: a real JSON array that's NOT a tool-call manifest
    (no leading `{"name": ...}` shape) must NOT be suppressed."""
    from livekit.agents.inference import llm as inf_llm
    pycall_sanitizer.install()

    self_mock = _make_self_mock(set())
    import threading
    thinking = threading.Event()

    text = '[1, 2, 3] is the list, sir.'
    c = _make_choice(text)
    inf_llm.LLMStream._parse_choice(self_mock, "resp_json_neg", c, thinking)
    assert c.delta.content == text, (
        f"false positive on plain JSON array: {c.delta.content!r}"
    )


# ── W-018: full-response leak triggers fallback acknowledgment ────────


def test_full_response_leak_injects_fallback_ack():
    """W-018 (2026-05-05): when the suppression covers the ENTIRE
    response (leak detected on first chunk, no real content before
    close), the sanitizer injects a synthetic 'Done, sir.' on the
    closing chunk so the user hears something instead of pure silence.

    Live-captured 2026-05-05 22:42–22:43 UTC: three supervisor turns
    leaked `task_done("Searched Amazon for shoes, sir.")` as content,
    sanitizer suppressed all three, persistence dropped them to
    0 chars, user reported 'JARVIS is silent' for ~6 minutes."""
    from livekit.agents.inference import llm as inf_llm
    pycall_sanitizer.install()

    self_mock = _make_self_mock(set())
    import threading
    thinking = threading.Event()

    chunks = ['task_done("', 'Searched Amazon, sir."', ')']
    voiced = []
    for content in chunks:
        c = _make_choice(content)
        inf_llm.LLMStream._parse_choice(self_mock, "resp_w018_1", c, thinking)
        voiced.append(c.delta.content or "")

    full = "".join(voiced)
    # Leak content stripped.
    assert "task_done" not in full
    assert "Searched Amazon" not in full
    # Fallback ack present so user hears SOMETHING.
    assert pycall_sanitizer._FALLBACK_ACK in full, (
        f"full-response leak suppressed but no fallback ack injected — "
        f"user gets silence. Got voiced={full!r}"
    )


def test_partial_leak_does_not_inject_fallback():
    """Negative test: if real content arrived BEFORE the leak, no
    fallback is needed (the user already heard the real reply).
    Today the sanitizer only fires when the leak is at the START of
    the stream, so this is automatically satisfied — but pin the
    behavior so a future refactor can't silently change it."""
    from livekit.agents.inference import llm as inf_llm
    pycall_sanitizer.install()

    self_mock = _make_self_mock(set())
    import threading
    thinking = threading.Event()

    chunks = [
        "I've opened Amazon. ",       # real prose first
        'Then task_done("done.")',    # leak after — sanitizer doesn't trigger
    ]
    voiced = []
    for content in chunks:
        c = _make_choice(content)
        inf_llm.LLMStream._parse_choice(self_mock, "resp_w018_2", c, thinking)
        voiced.append(c.delta.content or "")

    full = "".join(voiced)
    # Real content preserved.
    assert "I've opened Amazon" in full
    # The fallback should NOT be injected (we'd be doubling user-audible content).
    # Note: today's sanitizer also doesn't strip the trailing leak in this
    # case because it only checks first-chunk. That's a separate bug —
    # this test just pins that the fallback isn't appended after real prose.
    fallback = pycall_sanitizer._FALLBACK_ACK
    # The fallback should appear at most ONCE (and only if the sanitizer fired,
    # which it doesn't in this case). So count should be 0.
    assert full.count(fallback) == 0, (
        f"sanitizer injected fallback after real prose: {full!r}"
    )


# ── W-020: meta-silence streaming suppression ─────────────────────────


def test_suppresses_meta_silence_at_stream_start():
    """W-020 (2026-05-05): live-captured turn 993 22:52:21 — JARVIS
    replied with the literal text 'Silence.' to ambient TV audio.
    The recall-time meta-silence filter in jarvis_agent.py wasn't
    catching it at TTS time."""
    from livekit.agents.inference import llm as inf_llm
    pycall_sanitizer.install()

    self_mock = _make_self_mock(set())
    import threading
    thinking = threading.Event()

    for phrase in ["Silence.", "Silence, sir.", "Listening.",
                   "Standing by.", "Quiet."]:
        c = _make_choice(phrase)
        inf_llm.LLMStream._parse_choice(self_mock, f"resp_meta_{phrase}", c, thinking)
        assert (c.delta.content or "") == "", (
            f"meta-silence reply not suppressed: {phrase!r} → "
            f"{c.delta.content!r}"
        )


def test_suppresses_empty_output_template_leak():
    """2026-05-06 turn 1056: prompt rule 'Empty output.' for ambient
    audio was being treated as a literal-output template — JARVIS
    voiced 'empty output' eight times in 60 s. The prompt was
    rewritten AND the meta-silence regex extended; this pins both
    fixes so a future prompt rewrite can't silently regress."""
    from livekit.agents.inference import llm as inf_llm
    pycall_sanitizer.install()

    self_mock = _make_self_mock(set())
    import threading
    thinking = threading.Event()

    # All forms the LLM has been observed to literal-quote from the
    # prompt's silence rules. Each must suppress to empty string.
    for phrase in [
        "Empty output.",
        "empty output",
        "Empty output, sir.",
        "(empty output)",
        "No reply.",
        "No output.",
        "Nothing to say.",
        "Nothing.",
        "(silent)",
        "(no reply)",
    ]:
        c = _make_choice(phrase)
        inf_llm.LLMStream._parse_choice(
            self_mock, f"resp_empty_{hash(phrase)}", c, thinking
        )
        assert (c.delta.content or "") == "", (
            f"empty-output template not suppressed: {phrase!r} → "
            f"{c.delta.content!r}"
        )


def test_suppresses_zero_bytes_stage_direction_leak():
    """2026-05-28 (later): the DISCRETION section in soul.md said
    'Silence = zero bytes output. Produce empty bytes.' to instruct
    the LLM to remain silent for ambient audio. Live failure: the LLM
    treated the phrase as a stage-direction template and voiced
    "(zero bytes)" / "(zero bytes - ambient)" ~9 times in 90 s
    (pid=1261661, room=RM_6vQpjc64PuQ2). The soul phrasing was
    rewritten AND META_SILENCE_RE Branch B extended to catch the
    bracketed shape; this test pins the regex fix so a future
    prompt regression can't re-introduce the symptom.

    Boundary-gate path: sanitize_text_for_tts (used by the TTS
    synthesize() boundary gate in providers/tts.py) must return ""
    for every bracketed stage-direction shape the LLM is observed
    to emit. Real prose like "Zero bytes." (unbracketed reply about
    a file size) must pass through unchanged — only the bracketed
    stage-direction is suppressed."""
    from sanitizers.pycall import sanitize_text_for_tts

    # Bracketed stage-directions — must be suppressed to "".
    for phrase in [
        "(zero bytes)",
        "(zero bytes - ambient)",
        "(zero bytes — ambient)",
        "(zero bytes output)",
        "(empty bytes)",
        "(empty bytes - ambient)",
        "[zero bytes]",
    ]:
        assert sanitize_text_for_tts(phrase) == "", (
            f"bracketed zero/empty-bytes stage-direction not suppressed: "
            f"{phrase!r}"
        )

    # Unbracketed prose — must pass through unchanged (legitimate
    # reply about a file size, network packet, etc.).
    for phrase in [
        "Zero bytes.",
        "The file is zero bytes.",
        "It sent an empty bytes object.",
    ]:
        assert sanitize_text_for_tts(phrase) == phrase, (
            f"legit prose containing 'zero bytes' was suppressed: "
            f"{phrase!r}"
        )


def test_suppresses_meta_silence_split_across_chunks():
    """2026-05-06 turn 1063: Groq streamed 'Silence.' as multiple
    chunks, so the chunk-1 _META_SILENCE_RE check missed (partial
    'Sil' didn't match the full regex). Fix: meta-silence-watch
    envelope buffers early-stream chunks and rechecks the assembled
    prefix. This test drives the multi-chunk path explicitly."""
    from livekit.agents.inference import llm as inf_llm
    pycall_sanitizer.install()

    self_mock = _make_self_mock(set())
    import threading
    thinking = threading.Event()
    stream_id = "resp_multi_chunk_silence"

    # Three chunks that together spell "Silence." — none individually
    # match the META_SILENCE regex, but the assembled buffer does.
    chunks = ["Sil", "ence", "."]
    seen = []
    for piece in chunks:
        c = _make_choice(piece)
        inf_llm.LLMStream._parse_choice(self_mock, stream_id, c, thinking)
        seen.append(c.delta.content or "")

    # All three chunks should have been suppressed to "" — none of
    # the original characters reach TTS.
    assert seen == ["", "", ""], (
        f"multi-chunk meta-silence should suppress every chunk; got {seen!r}"
    )


def test_releases_buffer_when_not_meta_silence():
    """Negative case for the watch envelope: chunk 1 LOOKS like a
    silence prefix ('S') but the rest of the stream proves it's
    legitimate prose. Once we know it can't extend to a meta-
    silence phrase, the buffer must be released to TTS via the
    next chunk — NOT lost."""
    from livekit.agents.inference import llm as inf_llm
    pycall_sanitizer.install()

    self_mock = _make_self_mock(set())
    import threading
    thinking = threading.Event()
    stream_id = "resp_legit_prose_starting_with_s"

    # User asked "What's MVCC?" and JARVIS starts answering. Chunk 1
    # is "Si" — a valid prefix of "Silence" / "Silent". The watcher
    # should buffer. Chunk 2 "nce' " breaks the prefix-match (no
    # meta-silence phrase begins "since"), so the buffer releases
    # via this chunk's content.
    chunks_in = ["Sin", "ce all writes go through ", "the WAL, sir."]
    chunks_out = []
    for piece in chunks_in:
        c = _make_choice(piece)
        inf_llm.LLMStream._parse_choice(self_mock, stream_id, c, thinking)
        chunks_out.append(c.delta.content or "")

    # All original characters must reach TTS — the FIRST chunk that
    # decides "not silence" carries the released buffer.
    assembled = "".join(chunks_out)
    expected = "".join(chunks_in)
    assert assembled == expected, (
        f"assembled output should match input exactly; "
        f"got {assembled!r}, expected {expected!r}"
    )


def test_chunk1_meta_silence_match_suppresses_followups():
    """2026-05-06 turns 1082-1083: META_SILENCE_RE matched chunk 1
    ('Nothing') and suppressed it, but the chunk-1 path didn't set
    the meta-silence-suppressed envelope — so the LLM's CONTINUATION
    chunk (', sir.') leaked through as the spoken reply.

    Voice user heard JARVIS say literally 'sir' twice in a row with
    no actual reply content. Fix sets the envelope so all follow-up
    chunks suppress."""
    from livekit.agents.inference import llm as inf_llm
    pycall_sanitizer.install()

    self_mock = _make_self_mock(set())
    import threading
    thinking = threading.Event()
    stream_id = "resp_chunk1_meta_then_followup"

    # Reproduce the live-captured shape: chunk 1 is a complete
    # meta-silence phrase that matches the regex on first sight,
    # chunk 2 is a continuation that — pre-fix — leaked through.
    chunks_in = ["Nothing", ", sir.", " Nothing important to mention."]
    chunks_out = []
    for piece in chunks_in:
        c = _make_choice(piece)
        inf_llm.LLMStream._parse_choice(self_mock, stream_id, c, thinking)
        chunks_out.append(c.delta.content or "")

    # Every chunk after the chunk-1 META_SILENCE match must also be
    # suppressed — meta-silence is a single semantic reply; the
    # follow-up chunks are CONTINUATION, not a new reply.
    assert chunks_out == ["", "", ""], (
        f"chunk-1 meta-silence match should suppress all follow-ups; "
        f"got {chunks_out!r}"
    )


def test_releases_buffer_when_too_long():
    """If we keep buffering past _META_SILENCE_MAX_BUFFER chars
    without resolving, the buffer must release — meta-silence
    phrases are short, anything longer is real prose."""
    from livekit.agents.inference import llm as inf_llm
    from sanitizers.pycall import _META_SILENCE_MAX_BUFFER
    pycall_sanitizer.install()

    self_mock = _make_self_mock(set())
    import threading
    thinking = threading.Event()
    stream_id = "resp_long_legit_prose"

    # Chunk 1 is "Silence" (which IS a prefix of "silence" / a full
    # meta-silence phrase ON ITS OWN — but not punctuated). Then
    # chunk 2 keeps going — this should release because the assembled
    # text is longer than any meta-silence phrase.
    chunks_in = ["Silence is overrated, sir. Real engineers ship working code, ",
                 "not aphorisms."]
    chunks_out = []
    for piece in chunks_in:
        c = _make_choice(piece)
        inf_llm.LLMStream._parse_choice(self_mock, stream_id, c, thinking)
        chunks_out.append(c.delta.content or "")

    assembled = "".join(chunks_out)
    expected = "".join(chunks_in)
    assert assembled == expected, (
        f"assembled output should match input exactly; "
        f"got {assembled!r}, expected {expected!r}"
    )


def test_meta_silence_does_not_match_legitimate_empty_phrasing():
    """Negative: 'I have nothing to add' / 'Empty list' / 'silently
    crashed' must NOT trigger the meta-silence filter — they're
    real prose using the ban-listed words mid-sentence."""
    from livekit.agents.inference import llm as inf_llm
    pycall_sanitizer.install()

    self_mock = _make_self_mock(set())
    import threading
    thinking = threading.Event()

    for legit in [
        "I have nothing to add, sir.",
        "The script silently crashed in the background.",
        "The output was empty, but the test passed.",
        "There's nothing wrong with that.",
    ]:
        c = _make_choice(legit)
        inf_llm.LLMStream._parse_choice(
            self_mock, f"resp_legit_{hash(legit)}", c, thinking
        )
        assert c.delta.content == legit, (
            f"false positive on legitimate prose: {legit!r} → "
            f"{c.delta.content!r}"
        )


def test_meta_silence_filter_does_not_eat_legitimate_prose():
    """Negative test: the substring 'silence' inside a real reply
    must NOT trigger suppression."""
    from livekit.agents.inference import llm as inf_llm
    pycall_sanitizer.install()

    self_mock = _make_self_mock(set())
    import threading
    thinking = threading.Event()

    text = "The library is observing a moment of silence, sir."
    c = _make_choice(text)
    inf_llm.LLMStream._parse_choice(self_mock, "resp_meta_neg", c, thinking)
    assert c.delta.content == text, (
        f"false positive on legitimate use of 'silence': {c.delta.content!r}"
    )
