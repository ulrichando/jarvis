"""StatusServer — shape parity with voice-client `/status` + state transitions.

The tray's React poller at `src/desktop-tauri/src/hooks/useVoiceClient.js`
reads a specific subset of fields. If any field name drifts, the tray
icon stops reacting to direct-mode state and silently goes idle. This
test pins the shape so a future refactor can't break the indicator
silently.
"""
from __future__ import annotations

import asyncio

import aiohttp
import pytest

from direct_mode_status import StatusServer


# ── Ports — high range so tests don't collide with running voice-client
TEST_PORT_GEMINI = 18768
TEST_PORT_OPENAI = 18769


REQUIRED_FIELDS = {
    # Fields read by useVoiceClient.js. Adding fields is fine; removing
    # any of these breaks the tray icon's state machine.
    "connected",
    "agent_present",
    "muted",
    "listening",
    "speaking",
    "silent_mode",
    "tool_running",
    "agent_thinking",
    "sharing_screen",
    "cli_model",
    "speech_model",
    "tts_provider",
    # mode is direct-mode-specific so the tray can label the indicator
    # source if it ever wants to. Not strictly required by useVoiceClient.
    "mode",
}


@pytest.mark.asyncio
async def test_snapshot_has_all_voice_client_fields():
    s = StatusServer(port=TEST_PORT_GEMINI, mode="gemini")
    snap = s.snapshot()
    missing = REQUIRED_FIELDS - set(snap.keys())
    assert not missing, (
        f"StatusServer snapshot is missing fields the tray reads: {missing}. "
        f"Adding fields to voice-client's /status without mirroring them "
        f"here will silently break the direct-mode tray indicator."
    )


@pytest.mark.asyncio
async def test_initial_state_is_inactive():
    s = StatusServer(port=TEST_PORT_GEMINI, mode="gemini")
    snap = s.snapshot()
    assert snap["connected"]      is True   # HTTP server is up by construction
    assert snap["agent_present"]  is False  # upstream not yet connected
    assert snap["muted"]          is False
    assert snap["listening"]      is False
    assert snap["speaking"]       is False
    assert snap["silent_mode"]    is False
    assert snap["tool_running"]   is False
    assert snap["agent_thinking"] is False
    assert snap["sharing_screen"] is False
    assert snap["mode"]           == "gemini"


@pytest.mark.asyncio
async def test_setters_update_snapshot():
    s = StatusServer(port=TEST_PORT_OPENAI, mode="openai")
    s.set_agent_present(True)
    s.set_speaking(True)
    s.set_tool_running(True)
    snap = s.snapshot()
    assert snap["agent_present"] is True
    assert snap["speaking"]      is True
    assert snap["tool_running"]  is True
    # Idempotent re-set.
    s.set_speaking(True)
    assert s.snapshot()["speaking"] is True
    s.set_speaking(False)
    assert s.snapshot()["speaking"] is False


@pytest.mark.asyncio
async def test_setters_coerce_to_bool():
    """Set with non-bool values; output must still be strict bool so the
    JSON the tray polls doesn't expose Pythonic truthy garbage."""
    s = StatusServer(port=TEST_PORT_GEMINI, mode="gemini")
    s.set_speaking(1)    # type: ignore[arg-type]
    s.set_listening("")  # type: ignore[arg-type]
    s.set_tool_running(None)  # type: ignore[arg-type]
    snap = s.snapshot()
    assert snap["speaking"]     is True
    assert snap["listening"]    is False
    assert snap["tool_running"] is False


@pytest.mark.asyncio
async def test_http_status_endpoint_returns_snapshot():
    s = StatusServer(port=TEST_PORT_GEMINI, mode="gemini")
    s.set_agent_present(True)
    s.set_speaking(True)
    await s.start()
    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.get(f"http://127.0.0.1:{TEST_PORT_GEMINI}/status") as resp:
                assert resp.status == 200
                body = await resp.json()
        assert body["agent_present"] is True
        assert body["speaking"]      is True
        assert body["mode"]          == "gemini"
        assert REQUIRED_FIELDS.issubset(body.keys())
    finally:
        await s.stop()


@pytest.mark.asyncio
async def test_http_status_reflects_live_state_updates():
    """Tray polls at 10 Hz; setter→next poll must show the new value."""
    s = StatusServer(port=TEST_PORT_OPENAI, mode="openai")
    await s.start()
    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.get(f"http://127.0.0.1:{TEST_PORT_OPENAI}/status") as r:
                assert (await r.json())["speaking"] is False
            s.set_speaking(True)
            async with sess.get(f"http://127.0.0.1:{TEST_PORT_OPENAI}/status") as r:
                assert (await r.json())["speaking"] is True
            s.set_speaking(False)
            s.set_tool_running(True)
            async with sess.get(f"http://127.0.0.1:{TEST_PORT_OPENAI}/status") as r:
                body = await r.json()
                assert body["speaking"]     is False
                assert body["tool_running"] is True
    finally:
        await s.stop()


@pytest.mark.asyncio
async def test_serve_until_stops_cleanly_on_event():
    s = StatusServer(port=TEST_PORT_GEMINI, mode="gemini")
    stop_event = asyncio.Event()
    task = asyncio.create_task(s.serve_until(stop_event))
    # Give the server a tick to bind.
    await asyncio.sleep(0.05)
    async with aiohttp.ClientSession() as sess:
        async with sess.get(f"http://127.0.0.1:{TEST_PORT_GEMINI}/health") as r:
            assert r.status == 200
    stop_event.set()
    await asyncio.wait_for(task, timeout=2.0)
    # Port should be free now — a second StatusServer must bind on the
    # same port without conflict.
    s2 = StatusServer(port=TEST_PORT_GEMINI, mode="gemini")
    await s2.start()
    await s2.stop()


@pytest.mark.asyncio
async def test_cors_allows_tauri_webview():
    """The Tauri webview's fetch runs from a tauri://localhost origin;
    without CORS the /status fetch fails with no useful console error."""
    s = StatusServer(port=TEST_PORT_OPENAI, mode="openai")
    await s.start()
    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.get(f"http://127.0.0.1:{TEST_PORT_OPENAI}/status") as r:
                assert r.headers.get("Access-Control-Allow-Origin") == "*"
    finally:
        await s.stop()
