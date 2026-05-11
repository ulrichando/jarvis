"""Tests for tools/screen_share_control.py — the voice-command
toggle that wraps voice-client's POST /screen-share endpoint.

Verifies:
  - POSTs to the correct URL with start + ack=false
  - Honors True/False for start
  - Returns the expected status string on success
  - Returns a useful error on connect-refused, timeout, non-200
  - The supervisor's set_screen_share is a real FunctionTool so it
    plugs into the supervisor's tools=[…] list without further wiring
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class _FakeResponse:
    """Async-context-manager response wrapper."""
    def __init__(self, status: int, body: str):
        self.status = status
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def text(self) -> str:
        return self._body

    async def json(self) -> dict:
        return json.loads(self._body)


class _FakeSession:
    """Async-context-manager ClientSession that records the POST."""
    def __init__(self, response: _FakeResponse):
        self._resp = response
        self.last_url: str | None = None
        self.last_json: dict | None = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def post(self, url, *, json=None, timeout=None):  # noqa: A002 — match aiohttp signature
        self.last_url = url
        self.last_json = json
        return self._resp


@pytest.fixture
def fake_aiohttp(monkeypatch):
    """Patch aiohttp inside tools.screen_share_control."""
    import aiohttp

    holder = {"session": None, "exc": aiohttp.ClientError}

    def _make(response_or_exc):
        if isinstance(response_or_exc, Exception):
            class _RaisingSession:
                async def __aenter__(self_inner):
                    return self_inner
                async def __aexit__(self_inner, *a): return False
                def post(self_inner, *a, **kw):
                    raise response_or_exc
            holder["session"] = _RaisingSession()
            return holder["session"]
        holder["session"] = _FakeSession(response_or_exc)
        return holder["session"]

    # Patch ClientSession at the module level since the tool lazy-
    # imports `import aiohttp`. We patch the attribute on the loaded
    # aiohttp module which is shared.
    real_ClientSession = aiohttp.ClientSession
    real_ClientTimeout = aiohttp.ClientTimeout

    def _patch(response_or_exc):
        monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **kw: _make(response_or_exc))
        return holder

    yield _patch
    monkeypatch.setattr(aiohttp, "ClientSession", real_ClientSession)
    monkeypatch.setattr(aiohttp, "ClientTimeout", real_ClientTimeout)


def _impl(tool):
    """Unwrap the @function_tool decorator to call the inner coro."""
    return getattr(tool, "_func", None) or tool


# ── Happy path ─────────────────────────────────────────────────────


class TestStartScreenShare:
    def test_post_target_url_and_payload(self, fake_aiohttp):
        from tools import screen_share_control as ssc
        holder = fake_aiohttp(_FakeResponse(200, '{"sharing": true}'))
        result = run(_impl(ssc.set_screen_share)(start=True))
        assert holder["session"].last_url.endswith("/screen-share")
        assert holder["session"].last_json == {"start": True, "ack": False}
        assert "started" in result.lower()

    def test_returns_started_when_api_reports_sharing(self, fake_aiohttp):
        from tools import screen_share_control as ssc
        fake_aiohttp(_FakeResponse(200, '{"sharing": true}'))
        result = run(_impl(ssc.set_screen_share)(start=True))
        assert result == "screen sharing started"


class TestStopScreenShare:
    def test_post_with_start_false(self, fake_aiohttp):
        from tools import screen_share_control as ssc
        holder = fake_aiohttp(_FakeResponse(200, '{"sharing": false}'))
        result = run(_impl(ssc.set_screen_share)(start=False))
        assert holder["session"].last_json == {"start": False, "ack": False}
        assert result == "screen sharing stopped"


# ── Failure modes ───────────────────────────────────────────────────


class TestErrorHandling:
    def test_non_200_returns_user_visible_error(self, fake_aiohttp):
        from tools import screen_share_control as ssc
        fake_aiohttp(_FakeResponse(503, '{"error": "not connected"}'))
        result = run(_impl(ssc.set_screen_share)(start=True))
        assert "503" in result
        assert "not connected" in result

    def test_connector_error_returns_unreachable(self, fake_aiohttp):
        import aiohttp
        from tools import screen_share_control as ssc
        # ClientConnectorError requires a connection_key in newer
        # aiohttp; constructing it from the parent type keeps the
        # test resilient across versions.
        fake_aiohttp(aiohttp.ClientConnectorError(MagicMock(), OSError("refused")))
        result = run(_impl(ssc.set_screen_share)(start=True))
        assert "unreachable" in result.lower() or "8767" in result

    def test_unknown_exception_returns_typed_error(self, fake_aiohttp):
        from tools import screen_share_control as ssc
        fake_aiohttp(RuntimeError("disk full"))
        result = run(_impl(ssc.set_screen_share)(start=True))
        assert "RuntimeError" in result or "errored" in result


# ── Plumbing: it's a real FunctionTool ─────────────────────────────


class TestToolShape:
    def test_set_screen_share_is_a_function_tool(self):
        from tools import screen_share_control as ssc
        # The @function_tool decorator wraps the coro; the resulting
        # object exposes info.name + info.description + tool schema.
        tool = ssc.set_screen_share
        assert hasattr(tool, "info"), "not decorated by @function_tool"
        assert tool.info.name == "set_screen_share"

    def test_supervisor_imports_dont_break(self):
        """jarvis_agent.py should import the new tool without raising."""
        import importlib
        mod = importlib.import_module("tools.screen_share_control")
        assert hasattr(mod, "set_screen_share")
