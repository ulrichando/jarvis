"""HubClient.publish: enqueue event onto events:conversation stream
with a hub-assigned id."""
import json
import sys
from pathlib import Path

import fakeredis.aioredis
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "hub"))

import client


@pytest.mark.asyncio
async def test_publish_writes_to_events_stream():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    c = client.HubClient(redis=redis, source="voice")

    eid = await c.publish(
        type="conversation.message.created",
        session_id="s1",
        payload={"role": "user", "text": "hello"},
    )
    assert eid

    entries = await redis.xrange("events:conversation")
    assert len(entries) == 1
    _, fields = entries[0]
    evt = json.loads(fields["data"])
    assert evt["source"] == "voice"
    assert evt["type"] == "conversation.message.created"
    assert evt["session_id"] == "s1"
    assert evt["source_event_id"] == eid
    assert evt["payload"] == {"role": "user", "text": "hello"}
    assert "source_ts" in evt and isinstance(evt["source_ts"], int)


@pytest.mark.asyncio
async def test_publish_requires_source():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    with pytest.raises(ValueError):
        client.HubClient(redis=redis, source="")
