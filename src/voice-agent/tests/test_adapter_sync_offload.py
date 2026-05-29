"""Tests that sync tool handlers are offloaded via asyncio.to_thread.

JARVIS has tools with blocking sync handlers (x_search, vuln_check,
discord, image_gen) whose time.sleep / blocking-network calls would
freeze the event loop — and therefore TTS/STT/barge-in — if run inline.
The fix: the adapter's _run() wrapper must call
  asyncio.to_thread(handler, args)
for sync handlers and continue to `await handler(args)` for async ones.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, AsyncMock

import pytest

# Mirror the sys.path setup used by test_tool_adapter.py so that
# `import tools._adapter` works regardless of pytest's rootdir.
_VOICE_AGENT_ROOT = Path(__file__).resolve().parent.parent
if str(_VOICE_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_VOICE_AGENT_ROOT))

from tools import _adapter  # noqa: E402
from tools.registry import ToolEntry  # noqa: E402


def _make_entry(name: str, handler, *, is_async: bool) -> ToolEntry:
    """Build a minimal ToolEntry for testing the handler dispatch."""
    return ToolEntry(
        name=name,
        toolset="builtin",
        schema={"description": "test", "parameters": {"type": "object", "properties": {}}},
        handler=handler,
        check_fn=None,
        requires_env=[],
        is_async=is_async,
        description="test",
        emoji="",
    )


@pytest.mark.asyncio
async def test_sync_handler_offloaded_to_thread():
    """A sync handler MUST be run via asyncio.to_thread, not inline.

    Strategy: patch asyncio.to_thread in the adapter's namespace with a
    side_effect that still executes the function (so the result is correct)
    but lets us assert it was called.
    """
    def sync_handler(args):
        return {"ok": True}

    entry = _make_entry("dummy_sync", sync_handler, is_async=False)
    wrapped = _adapter._build_wrapped_handler(entry)

    async def fake_to_thread(fn, *a, **k):
        # Actually call the function so the return value flows through
        return fn(*a, **k)

    with patch("tools._adapter.asyncio.to_thread", side_effect=fake_to_thread) as mock_to_thread:
        result = await wrapped({})

    mock_to_thread.assert_called_once()
    # First positional arg to to_thread must be the sync handler
    assert mock_to_thread.call_args[0][0] is sync_handler
    assert result == str({"ok": True})


@pytest.mark.asyncio
async def test_async_handler_not_offloaded():
    """An async handler must NOT be passed to asyncio.to_thread."""
    async def async_handler(args):
        return {"ok": True}

    entry = _make_entry("dummy_async", async_handler, is_async=True)
    wrapped = _adapter._build_wrapped_handler(entry)

    with patch("tools._adapter.asyncio.to_thread") as mock_to_thread:
        result = await wrapped({})

    mock_to_thread.assert_not_called()
    assert result == str({"ok": True})
