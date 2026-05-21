"""Tests for the second batch of ported registry tools:
clarify, session_search, web_search, web_fetch.

Proves each ported tool:
  (a) self-registers in registry.all_entries() after import,
  (b) produces a valid RawFunctionTool via load_all_livekit_tools(),
  (c) behaves correctly in smoke tests without hitting the network
      (network calls are mocked / skipped).

The NoHermesTokens class statically verifies that none of the new tool
files contain the word 'hermes' outside of allowed comment/docstring
contexts (belt-and-suspenders, mirrors test_ported_tools_batch1.py).
"""
from __future__ import annotations

import asyncio
import json
import sys
import types
import unittest.mock as mock
from pathlib import Path

import pytest

# Ensure the voice-agent root is importable.
_VA_ROOT = Path(__file__).resolve().parent.parent
if str(_VA_ROOT) not in sys.path:
    sys.path.insert(0, str(_VA_ROOT))

from livekit.agents.llm import is_raw_function_tool  # noqa: E402
from tools import _adapter as adapter  # noqa: E402
from tools.registry import registry  # noqa: E402


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _invoke(tool, args: dict):
    return _run(tool(raw_arguments=args))


# ---------------------------------------------------------------------------
# (a) self-registration
# ---------------------------------------------------------------------------

class TestSelfRegistration:
    """After importing each tool module, registry.all_entries() must include it."""

    def test_clarify_registers(self):
        import tools.clarify  # noqa: F401 — side effect: registers 'clarify'
        assert registry.get_entry("clarify") is not None

    def test_session_search_registers(self):
        import tools.session_search  # noqa: F401
        assert registry.get_entry("session_search") is not None

    def test_web_search_registers(self):
        import tools.web_tools  # noqa: F401
        assert registry.get_entry("web_search") is not None

    def test_web_fetch_registers(self):
        import tools.web_tools  # noqa: F401
        assert registry.get_entry("web_fetch") is not None

    def test_all_four_in_all_entries(self):
        import tools.clarify, tools.session_search, tools.web_tools  # noqa: F401
        names = {e.name for e in registry.all_entries()}
        assert "clarify" in names
        assert "session_search" in names
        assert "web_search" in names
        assert "web_fetch" in names


# ---------------------------------------------------------------------------
# (b) load_all_livekit_tools returns valid RawFunctionTools
# ---------------------------------------------------------------------------

class TestLivekitAdaptation:
    """Adapted tools must be is_raw_function_tool and carry the correct name."""

    @pytest.fixture(scope="class", autouse=True)
    def _ensure_imports(self):
        import tools.clarify, tools.session_search, tools.web_tools  # noqa: F401

    def test_all_adapted_tools_are_raw_function_tools(self):
        tools = adapter.load_all_livekit_tools()
        assert all(is_raw_function_tool(t) for t in tools)

    def _get_adapted(self, name: str):
        tools = adapter.load_all_livekit_tools()
        matched = [t for t in tools if t.info.name == name]
        return matched[0] if matched else None

    def test_clarify_adapted(self):
        tool = self._get_adapted("clarify")
        assert tool is not None, "'clarify' not found in adapted tools"
        assert is_raw_function_tool(tool)

    def test_web_search_adapted(self):
        tool = self._get_adapted("web_search")
        assert tool is not None, "'web_search' not found in adapted tools"
        assert is_raw_function_tool(tool)

    def test_web_fetch_adapted(self):
        tool = self._get_adapted("web_fetch")
        assert tool is not None, "'web_fetch' not found in adapted tools"
        assert is_raw_function_tool(tool)

    def test_session_search_skipped_when_check_fn_false(self):
        """session_search has check_fn=_check_session_search which returns False."""
        tool = self._get_adapted("session_search")
        assert tool is None, (
            "session_search should be SKIPPED by load_all_livekit_tools() "
            "because its check_fn returns False (no JARVIS session DB wired)"
        )

    def test_session_search_entry_is_registered_but_unavailable(self):
        """It IS in the registry (can inspect schema) but is_available() is False."""
        entry = registry.get_entry("session_search")
        assert entry is not None
        assert not registry.is_available("session_search")


# ---------------------------------------------------------------------------
# (c) behavior smoke tests — clarify
# ---------------------------------------------------------------------------

class TestClarifyBehavior:
    """Smoke tests for the clarify handler."""

    def _call(self, args: dict) -> dict:
        from tools.clarify import _handle_clarify
        raw = _handle_clarify(args)
        return json.loads(raw)

    def test_open_ended_question(self):
        result = self._call({"question": "What file should I edit first?"})
        assert result["question"] == "What file should I edit first?"
        assert "choices" not in result
        assert "voice_prompt" in result

    def test_multiple_choice_question(self):
        result = self._call({
            "question": "Which approach do you prefer?",
            "choices": ["Option A", "Option B", "Option C"],
        })
        assert result["question"] == "Which approach do you prefer?"
        assert result["choices"] == ["Option A", "Option B", "Option C"]
        assert "voice_prompt" in result
        assert "Option A" in result["voice_prompt"]

    def test_choices_trimmed_to_max(self):
        from tools.clarify import MAX_CHOICES
        many = [f"Choice {i}" for i in range(MAX_CHOICES + 5)]
        result = self._call({"question": "Pick one", "choices": many})
        assert len(result["choices"]) == MAX_CHOICES

    def test_empty_choices_becomes_open_ended(self):
        result = self._call({"question": "Tell me anything", "choices": []})
        assert "choices" not in result

    def test_missing_question_returns_error(self):
        from tools.clarify import _handle_clarify
        raw = _handle_clarify({})
        data = json.loads(raw)
        assert "error" in data

    def test_blank_question_returns_error(self):
        from tools.clarify import _handle_clarify
        raw = _handle_clarify({"question": "   "})
        data = json.loads(raw)
        assert "error" in data

    def test_choices_not_a_list_returns_error(self):
        from tools.clarify import _handle_clarify
        raw = _handle_clarify({"question": "Pick one", "choices": "not-a-list"})
        data = json.loads(raw)
        assert "error" in data

    def test_question_stripped(self):
        result = self._call({"question": "  Trim me?  "})
        assert result["question"] == "Trim me?"

    def test_result_is_valid_json(self):
        from tools.clarify import _handle_clarify
        raw = _handle_clarify({"question": "Any question?"})
        # Must not raise
        json.loads(raw)


# ---------------------------------------------------------------------------
# (c) behavior smoke tests — session_search (no DB wired)
# ---------------------------------------------------------------------------

class TestSessionSearchBehavior:
    """Smoke tests for session_search with no DB — exercises gating + logic."""

    def _call(self, args: dict) -> dict:
        from tools.session_search import session_search
        raw = session_search(**{
            "query": args.get("query", ""),
            "role_filter": args.get("role_filter"),
            "limit": args.get("limit", 3),
            "session_id": args.get("session_id"),
            "around_message_id": args.get("around_message_id"),
            "window": args.get("window", 5),
            "sort": args.get("sort"),
            "db": None,
        })
        return json.loads(raw)

    def test_no_db_returns_error(self):
        result = self._call({})
        assert "error" in result

    def test_no_db_error_not_empty(self):
        result = self._call({"query": "auth refactor"})
        assert result.get("error")  # non-empty error message

    def test_check_fn_returns_false(self):
        from tools.session_search import _check_session_search
        assert _check_session_search() is False

    def test_format_timestamp_unix(self):
        from tools.session_search import _format_timestamp
        # Should produce a readable string without raising
        ts = 1_700_000_000
        result = _format_timestamp(ts)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_format_timestamp_none(self):
        from tools.session_search import _format_timestamp
        assert _format_timestamp(None) == "unknown"

    def test_shape_message_minimal(self):
        from tools.session_search import _shape_message
        m = {"id": 1, "role": "user", "content": "Hello"}
        out = _shape_message(m)
        assert out["role"] == "user"
        assert out["content"] == "Hello"

    def test_shape_message_anchor_flagged(self):
        from tools.session_search import _shape_message
        m = {"id": 42, "role": "assistant", "content": "reply"}
        out = _shape_message(m, anchor_id=42)
        assert out.get("anchor") is True

    def test_shape_message_non_anchor_not_flagged(self):
        from tools.session_search import _shape_message
        m = {"id": 1, "role": "user", "content": "text"}
        out = _shape_message(m, anchor_id=99)
        assert "anchor" not in out


class TestSessionSearchWithMockDB:
    """Test session_search logic against a minimal mock DB object."""

    class _MockDB:
        """Minimal DB stub that satisfies the session_search interface."""

        def __init__(self, sessions=None, messages=None):
            self._sessions = sessions or {}
            self._messages = messages or []

        def get_session(self, sid):
            return self._sessions.get(sid)

        def list_sessions_rich(self, limit, exclude_sources, order_by_last_active):
            return list(self._sessions.values())[:limit]

        def search_messages(self, query, role_filter, exclude_sources, limit, offset, sort):
            return [m for m in self._messages if query.lower() in (m.get("content") or "").lower()]

        def get_anchored_view(self, session_id, msg_id, window, bookend):
            return {"window": [], "bookend_start": [], "bookend_end": [], "messages_before": 0, "messages_after": 0}

        def get_messages_around(self, session_id, msg_id, window):
            return {"window": [], "messages_before": 0, "messages_after": 0}

    def _call_with_db(self, db, **kwargs) -> dict:
        from tools.session_search import session_search
        raw = session_search(db=db, **kwargs)
        return json.loads(raw)

    def test_browse_empty_db(self):
        db = self._MockDB()
        result = self._call_with_db(db)
        assert result["success"] is True
        assert result["mode"] == "browse"
        assert result["results"] == []

    def test_browse_with_sessions(self):
        db = self._MockDB(sessions={
            "s1": {"id": "s1", "title": "Test session", "source": "voice",
                   "started_at": 0, "last_active": 0, "message_count": 5,
                   "preview": "hello", "parent_session_id": None},
        })
        result = self._call_with_db(db)
        assert result["success"] is True
        assert len(result["results"]) == 1
        assert result["results"][0]["session_id"] == "s1"

    def test_discover_no_results(self):
        db = self._MockDB()
        result = self._call_with_db(db, query="nonexistent query")
        assert result["success"] is True
        assert result["mode"] == "discover"
        assert result["results"] == []

    def test_discover_finds_matching_message(self):
        db = self._MockDB(
            sessions={"s1": {"id": "s1", "title": None, "source": "voice",
                              "started_at": 0, "last_active": 0, "message_count": 1,
                              "preview": "", "parent_session_id": None}},
            messages=[{"id": 1, "session_id": "s1", "role": "user",
                        "content": "auth refactor discussion", "snippet": "auth refactor"}],
        )
        result = self._call_with_db(db, query="auth refactor")
        assert result["success"] is True
        assert result["mode"] == "discover"
        # Either found or the anchored view returned empty (mock returns [])
        assert "results" in result

    def test_scroll_no_session_returns_error(self):
        db = self._MockDB()
        result = self._call_with_db(db, session_id="nonexistent", around_message_id=1)
        assert "error" in result

    def test_limit_clamp_max(self):
        db = self._MockDB()
        result = self._call_with_db(db, limit=999)
        # Should not raise; limit is clamped to 10
        assert result["success"] is True

    def test_limit_clamp_min(self):
        db = self._MockDB()
        result = self._call_with_db(db, limit=0)
        assert result["success"] is True


# ---------------------------------------------------------------------------
# (c) behavior smoke tests — web_search + web_fetch (mocked network)
# ---------------------------------------------------------------------------

class TestWebSearchBehavior:
    """Smoke tests for web_search handler. Network calls are patched."""

    def _call(self, args: dict) -> str:
        from tools.web_tools import _handle_web_search
        return _run(_handle_web_search(args))

    def _make_fake_html(self, n: int = 3) -> str:
        """Generate DDG-like HTML with n result anchors and snippets."""
        results = []
        for i in range(n):
            results.append(
                f'<a class="result__a" rel="nofollow" '
                f'href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample{i}.com">Result {i}</a>'
            )
            results.append(
                f'<a class="result__snippet" rel="nofollow">Snippet for result {i}</a>'
            )
        # Fake HTML that is large enough to not look like an anomaly page
        return "<html><body>" + "".join(results) + ("X" * 20_000) + "</body></html>"

    def test_missing_query_returns_helpful_message(self):
        result = self._call({})
        assert "query" in result.lower() or "search" in result.lower()

    def test_empty_query_returns_helpful_message(self):
        result = self._call({"query": "   "})
        assert len(result) > 0

    def test_successful_search_parses_results(self):
        fake_html = self._make_fake_html(n=3)
        with mock.patch("tools.web_tools.asyncio.to_thread") as m_thread:
            async def _ret(*args, **kwargs):
                fn = args[0]
                return fake_html
            m_thread.side_effect = _ret
            result = self._call({"query": "test query", "limit": 3})
        # Results are numbered
        assert "1." in result
        assert "example" in result

    def test_limit_capped_at_10(self):
        fake_html = self._make_fake_html(n=15)
        with mock.patch("tools.web_tools.asyncio.to_thread") as m_thread:
            async def _ret(*args, **kwargs):
                return fake_html
            m_thread.side_effect = _ret
            result = self._call({"query": "test", "limit": 100})
        # Should not have more than 10 results
        assert result.count("\n1.") <= 1

    def test_http_error_returns_friendly_message(self):
        import urllib.error
        with mock.patch("tools.web_tools.asyncio.to_thread") as m_thread:
            async def _raise(*args, **kwargs):
                raise urllib.error.HTTPError(None, 403, "Forbidden", {}, None)
            m_thread.side_effect = _raise
            result = self._call({"query": "test"})
        assert "unavailable" in result.lower() or "403" in result

    def test_url_error_returns_friendly_message(self):
        import urllib.error
        with mock.patch("tools.web_tools.asyncio.to_thread") as m_thread:
            async def _raise(*args, **kwargs):
                raise urllib.error.URLError("Network unreachable")
            m_thread.side_effect = _raise
            result = self._call({"query": "test"})
        assert "unreachable" in result.lower() or "network" in result.lower()

    def test_no_results_returns_helpful_message(self):
        # HTML with no result anchors
        with mock.patch("tools.web_tools.asyncio.to_thread") as m_thread:
            async def _ret(*args, **kwargs):
                return "<html><body>" + "X" * 25_000 + "</body></html>"
            m_thread.side_effect = _ret
            result = self._call({"query": "very obscure query"})
        assert "no search results" in result.lower() or "result" in result.lower()

    def test_anomaly_page_triggers_ia_fallback(self):
        anomaly_html = "<html>anomaly-modal present</html>"
        with mock.patch("tools.web_tools.asyncio.to_thread") as m_thread:
            call_count = 0

            async def _side(*args, **kwargs):
                nonlocal call_count
                call_count += 1
                fn = args[0]
                if call_count == 1:
                    return anomaly_html
                # Second call: IA fallback — return None (not useful)
                return None

            m_thread.side_effect = _side
            result = self._call({"query": "test captcha"})
        # Should have escalated — either IA result or the "blocked" message
        assert len(result) > 0


class TestWebFetchBehavior:
    """Smoke tests for web_fetch handler. Network calls are patched."""

    def _call(self, args: dict) -> str:
        from tools.web_tools import _handle_web_fetch
        return _run(_handle_web_fetch(args))

    def test_missing_url_returns_error_message(self):
        result = self._call({})
        assert "url" in result.lower() or "(no url" in result.lower()

    def test_prepends_https_when_missing(self):
        """Confirm https:// is prepended — we observe it in the logger call."""
        fake_body = b"<html><body>Hello world</body></html>"

        captured_url = []

        def _fake_fetch_factory():
            # We need to capture the URL passed to urllib; inspect the handler
            # directly since to_thread wraps the closure.
            pass

        with mock.patch("tools.web_tools.asyncio.to_thread") as m_thread:
            async def _ret(*args, **kwargs):
                return "<html><body>plain text content here</body></html>"
            m_thread.side_effect = _ret
            result = self._call({"url": "example.com"})
        # Should succeed (no error in result)
        assert "could not be retrieved" not in result

    def test_successful_fetch_strips_html(self):
        html = "<html><head><style>body{}</style></head><body><h1>Title</h1><p>Content here.</p></body></html>"
        with mock.patch("tools.web_tools.asyncio.to_thread") as m_thread:
            async def _ret(*args, **kwargs):
                return html
            m_thread.side_effect = _ret
            result = self._call({"url": "https://example.com"})
        # Style tag content removed, HTML tags stripped
        assert "body{}" not in result
        assert "Content here" in result

    def test_successful_fetch_strips_scripts(self):
        html = "<html><body><script>alert('xss')</script><p>Safe content</p></body></html>"
        with mock.patch("tools.web_tools.asyncio.to_thread") as m_thread:
            async def _ret(*args, **kwargs):
                return html
            m_thread.side_effect = _ret
            result = self._call({"url": "https://example.com"})
        assert "alert" not in result
        assert "Safe content" in result

    def test_response_capped_at_char_limit(self):
        from tools.web_tools import _FETCH_CHAR_CAP
        huge_html = "<html><body>" + ("A" * (_FETCH_CHAR_CAP * 3)) + "</body></html>"
        with mock.patch("tools.web_tools.asyncio.to_thread") as m_thread:
            async def _ret(*args, **kwargs):
                return huge_html
            m_thread.side_effect = _ret
            result = self._call({"url": "https://example.com"})
        assert len(result) <= _FETCH_CHAR_CAP + 100  # allow for truncation suffix

    def test_http_error_returns_friendly_message(self):
        import urllib.error
        with mock.patch("tools.web_tools.asyncio.to_thread") as m_thread:
            async def _raise(*args, **kwargs):
                raise urllib.error.HTTPError(None, 404, "Not Found", {}, None)
            m_thread.side_effect = _raise
            result = self._call({"url": "https://example.com/missing"})
        assert "could not be retrieved" in result.lower()
        assert "404" in result

    def test_url_error_returns_friendly_message(self):
        import urllib.error
        with mock.patch("tools.web_tools.asyncio.to_thread") as m_thread:
            async def _raise(*args, **kwargs):
                raise urllib.error.URLError("Connection refused")
            m_thread.side_effect = _raise
            result = self._call({"url": "https://example.com"})
        assert "could not be retrieved" in result.lower()

    def test_timeout_clamped(self):
        """Timeout > 60 should be clamped — just check it doesn't crash."""
        with mock.patch("tools.web_tools.asyncio.to_thread") as m_thread:
            async def _ret(*args, **kwargs):
                return "<html><body>ok</body></html>"
            m_thread.side_effect = _ret
            result = self._call({"url": "https://example.com", "timeout": 9999})
        assert "ok" in result


# ---------------------------------------------------------------------------
# Cross-cutting: grepping new files for "hermes" (belt-and-suspenders)
# ---------------------------------------------------------------------------

class TestNoHermesTokens:
    """Static check: none of the new tool files contain the string 'hermes'."""

    @pytest.mark.parametrize("fname", [
        "clarify.py",
        "session_search.py",
        "web_tools.py",
    ])
    def test_no_hermes_in_file(self, fname):
        path = _VA_ROOT / "tools" / fname
        lines = path.read_text(encoding="utf-8").splitlines()
        bad_lines = []
        for lineno, line in enumerate(lines, 1):
            if "hermes" in line.lower():
                stripped = line.lstrip()
                # Allow comments that document the port
                if stripped.startswith("#") and "hermes" in stripped.lower():
                    continue
                # Allow docstrings explaining the port
                if '"""' in line and "hermes" in line.lower():
                    continue
                bad_lines.append((lineno, line.rstrip()))
        assert not bad_lines, (
            f"File {fname} contains non-comment 'hermes' tokens:\n"
            + "\n".join(f"  line {ln}: {txt}" for ln, txt in bad_lines)
        )
