"""Tests for the assistant_says data-channel publish added to
jarvis_agent._on_item (conversation_item_added handler).

The handler is registered inside entrypoint(ctx) so we can't import
and call it directly — but the publish logic is small and easily
extracted to a module-level helper for testability. This file tests
the helper, and Task 4's smoke test exercises the wired handler."""
from __future__ import annotations

import asyncio
import json
import unittest.mock as mock

import pytest


@pytest.mark.asyncio
async def test_publishes_for_assistant_with_text():
    """role=assistant + non-empty text → publish_data fires once."""
    from jarvis_agent import maybe_publish_assistant_says

    room = mock.MagicMock()
    room.local_participant.publish_data = mock.AsyncMock()
    item = mock.MagicMock(spec=["_jarvis_published_says"])
    # Strip the marker so first call publishes.
    if hasattr(item, "_jarvis_published_says"):
        del item._jarvis_published_says

    await maybe_publish_assistant_says(
        room=room, item=item, role="assistant", text="hello world"
    )
    room.local_participant.publish_data.assert_awaited_once()
    call_args = room.local_participant.publish_data.await_args
    payload_bytes = call_args.args[0]
    payload = json.loads(payload_bytes.decode("utf-8"))
    assert payload["type"] == "assistant_says"
    assert payload["text"] == "hello world"
    assert "ts_ms" in payload
    assert isinstance(payload["ts_ms"], int)


@pytest.mark.asyncio
async def test_idempotent_does_not_double_publish():
    """Same item passed twice → publish_data fires exactly once."""
    from jarvis_agent import maybe_publish_assistant_says

    room = mock.MagicMock()
    room.local_participant.publish_data = mock.AsyncMock()
    item = mock.MagicMock(spec=["_jarvis_published_says"])
    if hasattr(item, "_jarvis_published_says"):
        del item._jarvis_published_says

    await maybe_publish_assistant_says(
        room=room, item=item, role="assistant", text="first"
    )
    await maybe_publish_assistant_says(
        room=room, item=item, role="assistant", text="first"
    )
    assert room.local_participant.publish_data.await_count == 1


@pytest.mark.asyncio
async def test_skips_user_role():
    """role=user → no publish."""
    from jarvis_agent import maybe_publish_assistant_says

    room = mock.MagicMock()
    room.local_participant.publish_data = mock.AsyncMock()
    item = mock.MagicMock(spec=["_jarvis_published_says"])

    await maybe_publish_assistant_says(
        room=room, item=item, role="user", text="hello"
    )
    room.local_participant.publish_data.assert_not_called()


@pytest.mark.asyncio
async def test_skips_empty_text():
    """text="" → no publish."""
    from jarvis_agent import maybe_publish_assistant_says

    room = mock.MagicMock()
    room.local_participant.publish_data = mock.AsyncMock()
    item = mock.MagicMock(spec=["_jarvis_published_says"])

    await maybe_publish_assistant_says(
        room=room, item=item, role="assistant", text=""
    )
    room.local_participant.publish_data.assert_not_called()


@pytest.mark.asyncio
async def test_swallows_publish_exceptions():
    """If publish_data raises, helper logs and returns — does not propagate."""
    from jarvis_agent import maybe_publish_assistant_says

    room = mock.MagicMock()
    room.local_participant.publish_data = mock.AsyncMock(
        side_effect=RuntimeError("room closed")
    )
    item = mock.MagicMock(spec=["_jarvis_published_says"])

    # Should not raise.
    await maybe_publish_assistant_says(
        room=room, item=item, role="assistant", text="boom"
    )
