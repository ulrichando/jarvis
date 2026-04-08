"""ChannelRegistry — register, route and broadcast across all active channels."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.channels.base import ChannelMessage, ChannelPlugin

log = logging.getLogger("jarvis.channels")


class ChannelRegistry:
    """Holds all registered channel adapters and routes messages between them."""

    def __init__(self) -> None:
        self._channels: dict[str, ChannelPlugin] = {}
        self._handlers: list = []  # global inbound handlers

    # ── Registration ──────────────────────────────────────────────────

    def register(self, channel: ChannelPlugin) -> None:
        """Register a channel adapter.  Replaces any existing channel with the same id."""
        if channel.id in self._channels:
            log.info("Channel replaced: %s", channel.id)
        self._channels[channel.id] = channel
        log.debug("Channel registered: %s (%s)", channel.id, channel.name)

    def unregister(self, channel_id: str) -> bool:
        """Remove a channel by id.  Returns True if it existed."""
        if channel_id in self._channels:
            del self._channels[channel_id]
            log.debug("Channel unregistered: %s", channel_id)
            return True
        return False

    def get(self, channel_id: str) -> ChannelPlugin | None:
        return self._channels.get(channel_id)

    def list_channels(self) -> list[dict[str, Any]]:
        return [
            {
                "id": ch.id,
                "name": ch.name,
                "active": ch.is_active(),
                "capabilities": {
                    k: v for k, v in ch.capabilities.__dict__.items()
                },
            }
            for ch in self._channels.values()
        ]

    # ── Global inbound handler ────────────────────────────────────────

    def add_handler(self, handler) -> None:
        """Register a coroutine handler called for every inbound message on any channel."""
        self._handlers.append(handler)

    # ── Sending ───────────────────────────────────────────────────────

    async def send(self, channel_id: str, message: ChannelMessage) -> bool:
        """Send *message* to a specific channel.  Returns False if channel unknown."""
        ch = self._channels.get(channel_id)
        if ch is None:
            log.warning("send(): unknown channel %r", channel_id)
            return False
        try:
            return await ch.send(message)
        except Exception as e:
            log.error("Channel %s send error: %s", channel_id, e)
            return False

    async def broadcast(self, message: ChannelMessage, active_only: bool = True) -> int:
        """Send *message* to all (optionally active) channels.  Returns success count."""
        targets = [
            ch for ch in self._channels.values()
            if not active_only or ch.is_active()
        ]
        if not targets:
            return 0

        results = await asyncio.gather(
            *[ch.send(message) for ch in targets],
            return_exceptions=True,
        )
        ok = sum(1 for r in results if r is True)
        return ok

    # ── Lifecycle ─────────────────────────────────────────────────────

    async def start_all(self) -> None:
        """Call start() on every registered channel."""
        for ch in self._channels.values():
            try:
                await ch.start()
            except Exception as e:
                log.error("Channel %s start error: %s", ch.id, e)

    async def stop_all(self) -> None:
        """Call stop() on every registered channel."""
        for ch in self._channels.values():
            try:
                await ch.stop()
            except Exception as e:
                log.error("Channel %s stop error: %s", ch.id, e)


# ── Singleton ─────────────────────────────────────────────────────────────────

_registry: ChannelRegistry | None = None


def get_registry() -> ChannelRegistry:
    global _registry
    if _registry is None:
        _registry = ChannelRegistry()
    return _registry
