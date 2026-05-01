"""Tests for the tool-name sanitizer's parser.

The integration path (patched _run + synthetic ChatChunk) is exercised
live; here we test the pure-function recovery parser to lock the regex
and the safety guards in place.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tool_name_sanitizer import _try_recover


# Real error message captured live (Groq qwen3-32b, 2026-04-29):
_REAL_ERROR = (
    "tool call validation failed: attempted to call tool "
    "'recall_conversation {\"query\": \"total\"}' which was not in request.tools"
)

# Wrapped form — APIError nested inside APIConnectionError (matches the
# joined string our patched _run inspects).
_WRAPPED_ERROR = (
    "Connection error. || tool call validation failed: attempted to "
    "call tool 'web_search {\"q\": \"latest news\"}' which was not in "
    "request.tools"
)


def test_recovers_real_world_error():
    known = {"recall_conversation", "web_search", "bash"}
    res = _try_recover(_REAL_ERROR, known)
    assert res == ("recall_conversation", '{"query": "total"}')


def test_recovers_through_wrapped_chain():
    known = {"web_search"}
    res = _try_recover(_WRAPPED_ERROR, known)
    assert res == ("web_search", '{"q": "latest news"}')


def test_returns_none_when_recovered_name_not_in_tools():
    """Don't recover an unknown name — better to surface the real error."""
    known = {"bash", "screenshot"}  # no recall_conversation
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
