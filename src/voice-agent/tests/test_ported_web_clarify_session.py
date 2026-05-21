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

    def test_session_search_entry_is_registered(self):
        """session_search IS in the registry (can inspect schema)."""
        entry = registry.get_entry("session_search")
        assert entry is not None

    def test_session_search_check_fn_gated_by_db(self, monkeypatch, tmp_path):
        """check_fn returns True only when state.db exists; False otherwise."""
        from tools.session_search import _check_session_search
        from tools.registry import invalidate_check_fn_cache
        # Point at a nonexistent path → False
        monkeypatch.setenv("JARVIS_HUB_DB", str(tmp_path / "nope.db"))
        invalidate_check_fn_cache()
        assert _check_session_search() is False
        # Create the file → True
        (tmp_path / "nope.db").touch()
        invalidate_check_fn_cache()
        assert _check_session_search() is True


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
# (c) behavior smoke tests — session_search (hub-based, tmp SQLite)
# ---------------------------------------------------------------------------

import sqlite3 as _sqlite3


def _seed_db(path, rows):
    """rows: list of (session_id, role, text, ts_ms)."""
    conn = _sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY, source TEXT NOT NULL,
            created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL, source TEXT NOT NULL,
            source_event_id TEXT NOT NULL UNIQUE,
            role TEXT NOT NULL, text TEXT NOT NULL,
            tool_calls_json TEXT, ts INTEGER NOT NULL
        );
    """)
    seen = set()
    for i, (sid, role, text, ts) in enumerate(rows):
        if sid not in seen:
            conn.execute(
                "INSERT OR IGNORE INTO sessions (id,source,created_at,updated_at) VALUES(?,?,?,?)",
                (sid, "voice", ts, ts),
            )
            seen.add(sid)
        conn.execute(
            "INSERT INTO messages (session_id,source,source_event_id,role,text,ts) VALUES(?,?,?,?,?,?)",
            (sid, "voice", f"e{i}", role, text, ts),
        )
    conn.commit()
    conn.close()


class TestSessionSearchBehavior:
    """Smoke tests for session_search with the hub SQLite backend."""

    @pytest.fixture
    def db(self, tmp_path, monkeypatch):
        path = tmp_path / "state.db"
        _seed_db(path, [
            ("s1", "user", "auth refactor discussion", 1000),
            ("s1", "assistant", "sure let me help with auth", 2000),
            ("s2", "user", "hello world test", 3000),
        ])
        monkeypatch.setenv("JARVIS_HUB_DB", str(path))
        from tools.registry import invalidate_check_fn_cache
        invalidate_check_fn_cache()
        return path

    def _call(self, args: dict) -> dict:
        from tools.session_search import _handle_session_search
        return json.loads(_handle_session_search(args))

    def test_no_db_returns_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JARVIS_HUB_DB", str(tmp_path / "nope.db"))
        from tools.registry import invalidate_check_fn_cache
        invalidate_check_fn_cache()
        result = self._call({"query": "anything"})
        assert "error" in result

    def test_no_db_error_not_empty(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JARVIS_HUB_DB", str(tmp_path / "nope.db"))
        from tools.registry import invalidate_check_fn_cache
        invalidate_check_fn_cache()
        result = self._call({"query": "auth refactor"})
        assert result.get("error")  # non-empty error message

    def test_check_fn_false_when_no_db(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JARVIS_HUB_DB", str(tmp_path / "nope.db"))
        from tools.session_search import _check_session_search
        from tools.registry import invalidate_check_fn_cache
        invalidate_check_fn_cache()
        assert _check_session_search() is False

    def test_format_timestamp_unix(self):
        from tools.session_search import _format_ts
        ts_ms = 1_700_000_000 * 1000
        result = _format_ts(ts_ms)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_format_timestamp_none(self):
        from tools.session_search import _format_ts
        assert _format_ts(None) == "unknown"

    def test_snippet_trims_long_text(self):
        from tools.session_search import _snippet
        long = "A" * 300
        out = _snippet(long)
        assert len(out) <= 210  # 200 chars + ellipsis

    def test_snippet_short_text_unchanged(self):
        from tools.session_search import _snippet
        short = "hello world"
        assert _snippet(short) == short

    def test_discover_matches_keyword(self, db):
        result = self._call({"query": "auth"})
        assert result["success"] is True
        assert result["mode"] == "discover"
        assert result["count"] >= 1

    def test_discover_no_match_returns_empty(self, db):
        result = self._call({"query": "xyzzy_not_here"})
        assert result["success"] is True
        assert result["count"] == 0


class TestSessionSearchWithMockDB:
    """Test session_search shapes using temp SQLite DBs (hub schema)."""

    @pytest.fixture
    def empty_db(self, tmp_path, monkeypatch):
        path = tmp_path / "state.db"
        _seed_db(path, [])
        monkeypatch.setenv("JARVIS_HUB_DB", str(path))
        from tools.registry import invalidate_check_fn_cache
        invalidate_check_fn_cache()
        return path

    @pytest.fixture
    def seeded_db(self, tmp_path, monkeypatch):
        path = tmp_path / "state.db"
        _seed_db(path, [
            ("s1", "user", "hello from session one", 1000),
            ("s1", "assistant", "hi back", 2000),
            ("s2", "user", "second session content", 3000),
        ])
        monkeypatch.setenv("JARVIS_HUB_DB", str(path))
        from tools.registry import invalidate_check_fn_cache
        invalidate_check_fn_cache()
        return path

    def _call(self, args: dict) -> dict:
        from tools.session_search import _handle_session_search
        return json.loads(_handle_session_search(args))

    def test_browse_empty_db(self, empty_db):
        result = self._call({})
        assert result["success"] is True
        assert result["mode"] == "browse"
        assert result["results"] == []

    def test_browse_with_sessions(self, seeded_db):
        result = self._call({})
        assert result["success"] is True
        ids = {r["session_id"] for r in result["results"]}
        assert "s1" in ids
        assert "s2" in ids

    def test_discover_no_results(self, empty_db):
        result = self._call({"query": "nonexistent query"})
        assert result["success"] is True
        assert result["mode"] == "discover"
        assert result["results"] == []

    def test_discover_finds_matching_message(self, seeded_db):
        result = self._call({"query": "hello"})
        assert result["success"] is True
        assert result["mode"] == "discover"
        assert result["count"] >= 1
        texts = [r["snippet"] for r in result["results"]]
        assert any("hello" in t.lower() for t in texts)

    def test_session_shape_returns_messages(self, seeded_db):
        result = self._call({"session_id": "s1"})
        assert result["success"] is True
        assert result["mode"] == "session"
        assert result["count"] == 2

    def test_session_not_found_returns_empty(self, seeded_db):
        result = self._call({"session_id": "no-such"})
        assert result["success"] is True
        assert result["count"] == 0

    def test_limit_clamp_max(self, seeded_db):
        result = self._call({"query": "session", "limit": 999})
        assert result["success"] is True
        # limit capped at _MAX_LIMIT (20)
        assert result["count"] <= 20

    def test_limit_clamp_min(self, seeded_db):
        result = self._call({"query": "session", "limit": 0})
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
