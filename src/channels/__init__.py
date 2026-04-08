"""JARVIS channel abstraction layer.

A ChannelPlugin is any inbound/outbound messaging adapter — CLI, WebSocket,
desktop overlay, Telegram, SMS, etc.  All channels implement the same
Protocol so the brain can talk to them uniformly.

Usage:
    from src.channels import get_registry

    registry = get_registry()
    registry.register(MyTelegramChannel())

    # Send to all active channels:
    await registry.broadcast({"type": "response", "text": "Hello"})
"""

from src.channels.base import ChannelPlugin, ChannelMessage, ChannelCapabilities
from src.channels.registry import ChannelRegistry, get_registry

__all__ = [
    "ChannelPlugin",
    "ChannelMessage",
    "ChannelCapabilities",
    "ChannelRegistry",
    "get_registry",
]
