"""Tests for the SSE /events route + enqueue_event broadcast added to
VoiceClientHttpApi.

Boots the aiohttp app in a test client harness, subscribes a fake SSE
consumer, and verifies that enqueue_event surfaces as a data: line
on the wire. Also verifies subscriber add/remove on connect/disconnect
and queue back-pressure (drop oldest on full)."""
from __future__ import annotations

import asyncio
import json
import logging
import unittest.mock as mock

import pytest
from aiohttp.test_utils import TestClient, TestServer


def _make_api():
    """Construct a VoiceClientHttpApi with stub deps for HTTP-only tests."""
    from voice_client_http_api import VoiceClientHttpApi
    state = mock.MagicMock()
    return VoiceClientHttpApi(
        state=state,
        get_mic_pub=lambda: None,
        get_room=lambda: None,
        get_screen_share=lambda: None,
        restart_agent_unit=mock.AsyncMock(),
        log=logging.getLogger("test"),
    )


@pytest.mark.asyncio
async def test_subscribers_added_on_connect_and_removed_on_disconnect():
    api = _make_api()
    app = api.build_app()
    async with TestClient(TestServer(app)) as client:
        assert len(api._sse_subscribers) == 0
        async with client.get("/events") as resp:
            assert resp.status == 200
            assert resp.headers["Content-Type"].startswith("text/event-stream")
            # Wait briefly for the route to register its queue.
            for _ in range(20):
                if len(api._sse_subscribers) == 1:
                    break
                await asyncio.sleep(0.01)
            assert len(api._sse_subscribers) == 1
        # After client closes, finally block should remove subscriber.
        for _ in range(20):
            if len(api._sse_subscribers) == 0:
                break
            await asyncio.sleep(0.01)
        assert len(api._sse_subscribers) == 0


@pytest.mark.asyncio
async def test_enqueue_event_emits_data_line():
    api = _make_api()
    app = api.build_app()
    async with TestClient(TestServer(app)) as client:
        async with client.get("/events") as resp:
            # Wait for subscriber to register.
            for _ in range(20):
                if len(api._sse_subscribers) == 1:
                    break
                await asyncio.sleep(0.01)
            api.enqueue_event({"type": "assistant_says", "text": "hi"})
            # Read one SSE frame.
            line = await asyncio.wait_for(resp.content.readline(), timeout=2.0)
            assert line.startswith(b"data: ")
            payload = json.loads(line[len(b"data: "):].decode("utf-8").strip())
            assert payload == {"type": "assistant_says", "text": "hi"}


@pytest.mark.asyncio
async def test_enqueue_event_with_no_subscribers_is_noop():
    api = _make_api()
    # No SSE clients connected.
    api.enqueue_event({"type": "assistant_says", "text": "nobody home"})
    # Should not raise. Nothing else to assert — subscriber set is empty.
    assert len(api._sse_subscribers) == 0


@pytest.mark.asyncio
async def test_queue_full_drops_oldest():
    """Back-pressure: when a subscriber's queue is full, oldest is dropped
    and newest is kept (LIFO-ish bounded behavior)."""
    api = _make_api()
    # Inject a tiny queue directly to force overflow.
    q: asyncio.Queue = asyncio.Queue(maxsize=2)
    api._sse_subscribers.add(q)
    api.enqueue_event({"i": 1})
    api.enqueue_event({"i": 2})
    api.enqueue_event({"i": 3})  # overflow — should drop {i:1} and keep 2, 3
    items = [q.get_nowait(), q.get_nowait()]
    indices = sorted(item["i"] for item in items)
    assert indices == [2, 3]


@pytest.mark.asyncio
async def test_cors_preflight_for_events():
    """OPTIONS /events should hit the existing CORS wildcard.

    The existing `cors` handler returns 204 (HTTP standard for OPTIONS
    preflight) with `Access-Control-Allow-Origin: *`. We assert the
    CORS header is present — the status code itself is an aiohttp
    detail (204 No Content), but `2xx` is what matters for browsers.
    """
    api = _make_api()
    app = api.build_app()
    async with TestClient(TestServer(app)) as client:
        async with client.options("/events") as resp:
            assert 200 <= resp.status < 300
            assert resp.headers.get("Access-Control-Allow-Origin") == "*"
