"""Tests for the Chrome auto-launch path inside `_browser_ext_base.post`.

Live failure 2026-05-13 01:38 UTC. User closed Chrome between two
"open YouTube" voice attempts. The pre_transfer hook on
`transfer_to_browser` only fires when the supervisor invokes a fresh
handoff — but after a `task_done` REFUSED bailout, the subagent
re-activates without going through the hook. The bridge POST then
hit a disconnected extension and the subagent bailed.

The fix puts the launch responsibility INSIDE `post()` itself, so
EVERY `ext_*` call has the Chrome-up guarantee regardless of which
codepath activated the subagent.

These tests mock both subprocess (Chrome launch) and aiohttp (bridge
HTTP) so we exercise the retry logic deterministically without
spawning real Chrome.
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest


@pytest.fixture
def base(monkeypatch):
    """Reset env flags + grab the module fresh per test."""
    monkeypatch.delenv("JARVIS_BROWSER_AUTOLAUNCH_DISABLE", raising=False)
    from tools import _browser_ext_base
    # Shorten the wait/poll so tests don't sit 8s per assertion.
    monkeypatch.setattr(_browser_ext_base, "_AUTOLAUNCH_WAIT_S", 0.5)
    monkeypatch.setattr(_browser_ext_base, "_AUTOLAUNCH_POLL_S", 0.05)
    return _browser_ext_base


# ── Mock helpers ─────────────────────────────────────────────────


class _FakeResponse:
    def __init__(self, *, status: int, body: dict):
        self.status = status
        self._body = body
    async def __aenter__(self):
        return self
    async def __aexit__(self, *_):
        return False
    async def json(self):
        return self._body
    async def text(self):
        import json
        return json.dumps(self._body)


class _FakeSession:
    """Replace `aiohttp.ClientSession()` — script POST + GET responses."""

    def __init__(self, post_responses, status_responses):
        # post_responses: list of {"status": int, "body": dict}
        # status_responses: list of {"status": int, "body": dict} for /api/ext_status
        self._post_responses = list(post_responses)
        self._status_responses = list(status_responses)
        self.post_calls = []
        self.status_calls = 0

    async def __aenter__(self):
        return self
    async def __aexit__(self, *_):
        return False

    def post(self, url, json=None, headers=None):
        self.post_calls.append({"url": url, "json": json})
        # Pop next scripted response (or repeat the last if exhausted).
        if self._post_responses:
            resp = self._post_responses.pop(0)
        else:
            resp = {"status": 200, "body": {"ok": False, "error": "no more scripted"}}
        return _FakeResponse(status=resp["status"], body=resp["body"])

    def get(self, url, timeout=None):
        self.status_calls += 1
        if self._status_responses:
            resp = self._status_responses.pop(0)
        else:
            resp = {"status": 200, "body": {"connected": False}}
        return _FakeResponse(status=resp["status"], body=resp["body"])


def _patch_session(base, session):
    return patch.object(base.aiohttp, "ClientSession", lambda *_a, **_k: session)


def _patch_subprocess(monkeypatch, base, launch_succeeds=True):
    """Replace `asyncio.create_subprocess_exec` so Chrome launch is
    instant + scriptable. Returns a state dict the test can inspect."""
    state = {"launch_calls": 0}

    class _FakeProc:
        returncode = 0 if launch_succeeds else 1
        async def wait(self):
            return self.returncode

    async def _fake_exec(*args, **kwargs):
        state["launch_calls"] += 1
        if not launch_succeeds:
            raise FileNotFoundError("setsid not on PATH (mock)")
        return _FakeProc()

    monkeypatch.setattr(base.asyncio, "create_subprocess_exec", _fake_exec)
    return state


# ── Tests ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_post_success_no_launch(base, monkeypatch):
    """When the bridge returns ok on the first call, no launch
    attempts fire — fast path."""
    launch_state = _patch_subprocess(monkeypatch, base)
    session = _FakeSession(
        post_responses=[{"status": 200, "body": {"ok": True, "url": "https://x.com/"}}],
        status_responses=[],
    )
    with _patch_session(base, session):
        result = await base.post("navigate", url="https://x.com/")
    assert result == {"ok": True, "url": "https://x.com/"}
    assert launch_state["launch_calls"] == 0
    assert len(session.post_calls) == 1


@pytest.mark.asyncio
async def test_post_503_triggers_launch_and_retry(base, monkeypatch):
    """The headline fix. First POST returns 503 'extension not
    connected'. Hook launches Chrome, polls ext_status until True,
    retries POST — second POST succeeds."""
    launch_state = _patch_subprocess(monkeypatch, base)
    session = _FakeSession(
        post_responses=[
            {"status": 503, "body": {"ok": False, "error": "extension not connected"}},
            {"status": 200, "body": {"ok": True, "url": "https://www.youtube.com/"}},
        ],
        # After launch, the very first ext_status check sees connected.
        status_responses=[{"status": 200, "body": {"connected": True}}],
    )
    with _patch_session(base, session):
        result = await base.post("navigate", url="https://www.youtube.com/")
    # Second-try response surfaces.
    assert result["ok"] is True
    assert result["url"] == "https://www.youtube.com/"
    # Chrome launch fired exactly once.
    assert launch_state["launch_calls"] == 1
    # POST happened twice (initial + retry after launch).
    assert len(session.post_calls) == 2


@pytest.mark.asyncio
async def test_post_503_then_extension_never_connects(base, monkeypatch):
    """If Chrome launch happens but the extension never registers
    within the wait budget, return the ORIGINAL 503 response (don't
    retry, since the bridge would just fail the same way)."""
    launch_state = _patch_subprocess(monkeypatch, base)
    session = _FakeSession(
        post_responses=[
            {"status": 503, "body": {"ok": False, "error": "extension not connected"}},
        ],
        # Many False reads exhausts the wait budget.
        status_responses=[{"status": 200, "body": {"connected": False}}] * 50,
    )
    with _patch_session(base, session):
        result = await base.post("navigate", url="https://www.youtube.com/")
    assert result["ok"] is False
    assert "extension not connected" in result["error"]
    assert launch_state["launch_calls"] == 1
    # No retry — only the initial POST happened.
    assert len(session.post_calls) == 1


@pytest.mark.asyncio
async def test_post_503_launch_failure_returns_original_error(base, monkeypatch):
    """If `setsid -f google-chrome` itself fails to spawn, surface
    the original 503 to the caller — don't retry the POST."""
    launch_state = _patch_subprocess(monkeypatch, base, launch_succeeds=False)
    session = _FakeSession(
        post_responses=[
            {"status": 503, "body": {"ok": False, "error": "extension not connected"}},
        ],
        status_responses=[],
    )
    with _patch_session(base, session):
        result = await base.post("navigate", url="https://www.youtube.com/")
    assert result["ok"] is False
    assert "extension not connected" in result["error"]
    assert launch_state["launch_calls"] == 1
    # No retry — launch failed.
    assert len(session.post_calls) == 1


@pytest.mark.asyncio
async def test_autolaunch_disable_env_blocks_relaunch(base, monkeypatch):
    """`JARVIS_BROWSER_AUTOLAUNCH_DISABLE=1` reverts to pre-2026-05-13
    behavior (no auto-launch on 503)."""
    monkeypatch.setenv("JARVIS_BROWSER_AUTOLAUNCH_DISABLE", "1")
    # Need to reload the env-gated module-level constant.
    import importlib
    importlib.reload(base)
    monkeypatch.setattr(base, "_AUTOLAUNCH_WAIT_S", 0.5)
    monkeypatch.setattr(base, "_AUTOLAUNCH_POLL_S", 0.05)

    launch_state = _patch_subprocess(monkeypatch, base)
    session = _FakeSession(
        post_responses=[
            {"status": 503, "body": {"ok": False, "error": "extension not connected"}},
        ],
        status_responses=[],
    )
    with _patch_session(base, session):
        result = await base.post("navigate", url="https://www.youtube.com/")
    assert result["ok"] is False
    assert launch_state["launch_calls"] == 0
    assert len(session.post_calls) == 1


@pytest.mark.asyncio
async def test_post_non_503_error_no_launch(base, monkeypatch):
    """A 500 from the bridge (or a different error string) should NOT
    trigger auto-launch — only specifically 'extension not connected'
    indicates a closed-Chrome state that launching could fix."""
    launch_state = _patch_subprocess(monkeypatch, base)
    session = _FakeSession(
        post_responses=[
            {"status": 504, "body": {"ok": False, "error": "timeout waiting on extension"}},
        ],
        status_responses=[],
    )
    with _patch_session(base, session):
        result = await base.post("navigate", url="https://x.com/")
    assert result["ok"] is False
    assert launch_state["launch_calls"] == 0
