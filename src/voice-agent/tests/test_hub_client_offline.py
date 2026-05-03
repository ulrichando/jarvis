"""HubClient.publish must not raise when Redis is unreachable. It
buffers up to OFFLINE_MAX events in-memory and flushes on demand."""
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "hub"))

import client


@pytest.mark.asyncio
async def test_publish_buffers_when_redis_down():
    fake = AsyncMock()
    fake.xadd.side_effect = ConnectionError("redis down")
    c = client.HubClient(redis=fake, source="voice")

    eid = await c.publish(
        type="conversation.message.created",
        session_id="s1",
        payload={"role": "user", "text": "hi"},
    )
    assert eid
    assert len(c._offline) == 1


@pytest.mark.asyncio
async def test_flush_offline_queue_replays_in_order():
    fake = AsyncMock()
    # First two calls fail, then succeed for the rest.
    fake.xadd.side_effect = [
        ConnectionError("down"),
        ConnectionError("down"),
        b"1234-0",
        b"1234-1",
    ]
    c = client.HubClient(redis=fake, source="voice")
    await c.publish(
        type="conversation.message.created",
        session_id="s",
        payload={"role": "user", "text": "a"},
    )
    await c.publish(
        type="conversation.message.created",
        session_id="s",
        payload={"role": "user", "text": "b"},
    )
    assert len(c._offline) == 2

    flushed = await c.flush_offline_queue()
    assert flushed == 2
    assert len(c._offline) == 0


@pytest.mark.asyncio
async def test_offline_queue_caps_at_max():
    fake = AsyncMock()
    fake.xadd.side_effect = ConnectionError("down")
    c = client.HubClient(redis=fake, source="voice")
    for i in range(150):
        await c.publish(
            type="conversation.message.created",
            session_id="s",
            payload={"role": "user", "text": str(i)},
        )
    assert len(c._offline) == client.HubClient.OFFLINE_MAX
    # Oldest dropped (FIFO with maxlen): first item should be event #50
    first_evt = json.loads(c._offline[0]["data"])
    assert first_evt["payload"]["text"] == "50"
