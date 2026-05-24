"""Tests for the tool-name sanitizer's parser.

The integration path (patched _run + synthetic ChatChunk) is exercised
live; here we test the pure-function recovery parser to lock the regex
and the safety guards in place.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sanitizers.tool_name import _try_recover


# Captured live (Groq qwen3-32b, 2026-04-29) — name + JSON body
# concatenated into the `name` field instead of being properly split:
_REAL_ERROR = (
    "tool call validation failed: attempted to call tool "
    "'web_search {\"query\": \"total\"}' which was not in request.tools"
)

# Wrapped form — APIError nested inside APIConnectionError (matches the
# joined string our patched _run inspects).
_WRAPPED_ERROR = (
    "Connection error. || tool call validation failed: attempted to "
    "call tool 'web_search {\"q\": \"latest news\"}' which was not in "
    "request.tools"
)


def test_recovers_real_world_error():
    known = {"web_search", "bash"}
    res = _try_recover(_REAL_ERROR, known)
    assert res == ("web_search", '{"query": "total"}')


def test_recovers_through_wrapped_chain():
    known = {"web_search"}
    res = _try_recover(_WRAPPED_ERROR, known)
    assert res == ("web_search", '{"q": "latest news"}')


def test_returns_none_when_recovered_name_not_in_tools():
    """Don't recover an unknown name — better to surface the real error."""
    known = {"bash", "screenshot"}  # no web_search
    res = _try_recover(_REAL_ERROR, known)
    assert res is None


def test_returns_none_for_unrelated_error():
    known = {"bash"}
    assert _try_recover("HTTP 500 internal error", known) is None
    assert _try_recover("", known) is None
    assert _try_recover("connection refused", known) is None


def test_returns_none_when_malformed_name_has_no_json_body():
    """Pattern requires `name {<json>}` shape. A garbage tail like
    'do_thing TOTAL' won't recover — we don't guess at args."""
    err = (
        "tool call validation failed: attempted to call tool "
        "'do_thing TOTAL' which was not in request.tools"
    )
    res = _try_recover(err, {"do_thing"})
    assert res is None


def test_handles_name_eq_json_form():
    """Captured live 2026-05-01: Groq llama emitted
    `web_fetch={"url":"...","timeout":"15"}` — no space, `=` sep."""
    err = (
        "tool call validation failed: attempted to call tool "
        "'web_fetch={\"url\": \"https://example.com\", \"timeout\": \"15\"}' "
        "which was not in request.tools"
    )
    res = _try_recover(err, {"web_fetch"})
    assert res is not None
    name, args = res
    assert name == "web_fetch"
    assert "url" in args
    assert "example.com" in args


def test_handles_name_colon_json_form():
    """Defensive — some providers may use `name:{...}` shape."""
    err = (
        "tool call validation failed: attempted to call tool "
        "'bash:{\"cmd\": \"ls\"}' which was not in request.tools"
    )
    res = _try_recover(err, {"bash"})
    assert res == ("bash", '{"cmd": "ls"}')


def test_handles_multi_arg_json():
    err = (
        "tool call validation failed: attempted to call tool "
        "'launch_app {\"binary\": \"google-chrome\", \"args\": \"--new-window\"}' "
        "which was not in request.tools"
    )
    res = _try_recover(err, {"launch_app"})
    assert res is not None
    name, args = res
    assert name == "launch_app"
    assert '"binary"' in args
    assert '"google-chrome"' in args


def test_handles_nested_braces_in_json():
    """JSON args with nested object — the regex is greedy on the JSON body."""
    err = (
        "tool call validation failed: attempted to call tool "
        "'do_thing {\"opts\": {\"deep\": true}}' which was not in request.tools"
    )
    res = _try_recover(err, {"do_thing"})
    assert res == ("do_thing", '{"opts": {"deep": true}}')


def test_does_not_recover_blank_tool_list():
    """Edge: empty known-tools set means we can't validate — fail safely."""
    res = _try_recover(_REAL_ERROR, set())
    assert res is None


# ── Helpers added in inline-execution rewrite (2026-05-01) ──────────


def test_tool_takes_context_via_param_name():
    """A function with a `context` parameter should be flagged as
    needing RunContext — sanitizer can't recover those inline."""
    from sanitizers.tool_name import _tool_takes_context

    async def needs_context(context, request: str) -> str:
        return f"got {request}"

    # Wrap minimally to mimic FunctionTool's _func attribute
    class FakeTool:
        _func = staticmethod(needs_context)

    assert _tool_takes_context(FakeTool()) is True


def test_tool_takes_context_via_annotation():
    """If the parameter is named differently but typed RunContext,
    we should still detect it."""
    from sanitizers.tool_name import _tool_takes_context

    # Use a string annotation since we don't import RunContext here
    async def needs_typed(c: "RunContext", request: str) -> str:
        return request

    class FakeTool:
        _func = staticmethod(needs_typed)

    # The annotation repr will contain "RunContext" as a string
    # due to the forward reference.
    needs_typed.__annotations__["c"] = "RunContext"
    assert _tool_takes_context(FakeTool()) is True


def test_tool_no_context_returns_false():
    """get_location-style tool (no args, no context) — recoverable inline."""
    from sanitizers.tool_name import _tool_takes_context

    async def free_func() -> str:
        return "ok"

    class FakeTool:
        _func = staticmethod(free_func)

    assert _tool_takes_context(FakeTool()) is False


def test_format_result_string_passthrough():
    from sanitizers.tool_name import _format_result
    assert _format_result("Columbus, Ohio") == "Columbus, Ohio"


def test_format_result_tuple_extracts_string():
    """Subagent transfer tools return (Agent, str). Use the str."""
    from sanitizers.tool_name import _format_result

    class FakeAgent: pass
    assert _format_result((FakeAgent(), "On it.")) == "On it."


def test_format_result_dict_picks_message_field():
    from sanitizers.tool_name import _format_result
    out = _format_result({"message": "Got it.", "status": "ok"})
    assert out == "Got it."


def test_format_result_dict_with_no_known_field_serializes():
    from sanitizers.tool_name import _format_result
    out = _format_result({"foo": "bar"})
    assert "foo" in out and "bar" in out


def test_format_result_falls_back_to_str():
    from sanitizers.tool_name import _format_result
    assert _format_result(42) == "42"


# ── W-014: inline-execute path now re-emits as FunctionToolCall ──────


def test_inline_recovery_emits_function_tool_call_not_content():
    """W-014 (2026-05-05): the previous inline-execute path called the
    tool's underlying function and emitted the result as
    `role: "assistant", content: <result>`. Two failure modes:
      1. TTS read the dict repr aloud when the tool returned structured
         data — live-observed with ext_navigate's page-headings dict.
      2. LLM saw the result as `role: "assistant"`, never as
         `role: "tool"`, so the next inference didn't know a tool had
         returned and re-attempted the same call (loop).

    The fix: re-emit the recovered call as a proper FunctionToolCall
    chunk and let the framework's dispatch loop run it. This test pins
    the *source-level* invariants so a future "optimize by going
    inline again" can't silently regress without removing the
    documented patterns. A live-integration test would need the full
    livekit streaming machinery; the source-level invariant is durable
    and order-independent.
    """
    import inspect
    import sanitizers.tool_name as tool_name_sanitizer

    src = inspect.getsource(tool_name_sanitizer)

    # Bad pattern 1: emit a content chunk in the recovery branch.
    assert "Emit ONE chunk with plain content" not in src, (
        "the inline-execute path's content-emit pattern is back; this "
        "regresses to W-014 (TTS-of-dict + LLM loop)."
    )

    # Bad pattern 2: call the tool inline.
    assert "result = tool(**kwargs)" not in src, (
        "the sanitizer is calling tool(**kwargs) inline again — that "
        "regresses to W-014. The framework's dispatch loop must run "
        "the tool, not the sanitizer."
    )

    # Good pattern: re-emit as FunctionToolCall.
    assert "FunctionToolCall(" in src, (
        "no FunctionToolCall re-emit in the sanitizer — recovery is "
        "broken (or executing inline, which has W-014 failure modes)."
    )
    assert "tool_calls=[tool_call]" in src, (
        "recovery chunk must set tool_calls (so framework dispatches), "
        "not content (which TTS reads aloud)."
    )

    # Defensive: rationale comment must be present so the next
    # maintainer reading the file understands why it's structured this
    # way and doesn't "optimize" back to inline-execute.
    src_l = src.lower()
    assert (
        "let framework dispatch" in src_l
        or "framework's dispatch" in src_l
        or "framework's normal tool-dispatch" in src_l
        or "framework's tool-dispatch loop" in src_l
    ), (
        "the recovery code must comment the 'let framework dispatch' "
        "rationale — without it a future maintainer rebuilds the "
        "inline-execute path and reintroduces W-014."
    )
