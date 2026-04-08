"""ChannelPlugin Protocol — unified interface for all messaging adapters.

Mirrors OpenClaw's src/channels/plugins/types.plugin.ts.

Every channel (CLI, WebSocket web client, desktop overlay, Telegram,
WhatsApp, SMS, …) implements this Protocol.  The brain and routing layer
talk through this interface — they never import a concrete channel class
directly.

Minimal implementation example
───────────────────────────────
    class TelegramChannel:
        id = "telegram"
        name = "Telegram"
        capabilities = ChannelCapabilities(markdown=True, voice=False, images=True)

        async def send(self, message: ChannelMessage) -> bool: ...
        async def on_message(self, handler): self._handler = handler
        async def start(self): ...
        async def stop(self): ...
        def is_active(self) -> bool: ...
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol, runtime_checkable


# ── Message model ─────────────────────────────────────────────────────────────

@dataclass
class ChannelMessage:
    """A message flowing between the brain and a channel adapter."""

    # Routing
    channel_id: str = ""        # which channel sent/receives this
    session_id: str = ""        # conversation session
    user_id: str = ""           # sender identity (channel-specific)

    # Content
    text: str = ""
    role: str = "user"          # "user" | "assistant" | "system"
    message_type: str = "text"  # "text" | "voice" | "image" | "action"

    # Optional media
    audio_bytes: bytes | None = None
    image_bytes: bytes | None = None
    image_mime: str = ""

    # Metadata
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=lambda: __import__("time").time())


# ── Capabilities declaration ──────────────────────────────────────────────────

@dataclass
class ChannelCapabilities:
    """Declare what a channel can send/receive."""
    markdown: bool = False      # renders markdown
    voice: bool = False         # can send/receive audio
    images: bool = False        # can send/receive images
    files: bool = False         # can send/receive file attachments
    reactions: bool = False     # emoji reactions
    threads: bool = False       # threaded replies
    persistent: bool = True     # messages survive restart


# ── Protocol ──────────────────────────────────────────────────────────────────

MessageHandler = Callable[[ChannelMessage], Awaitable[None]]


@runtime_checkable
class ChannelPlugin(Protocol):
    """Standard interface all channel adapters must implement."""

    # Identity
    id: str                          # unique slug, e.g. "cli", "web", "telegram"
    name: str                        # human-readable name
    capabilities: ChannelCapabilities

    async def send(self, message: ChannelMessage) -> bool:
        """Send *message* to the channel.  Returns True on success."""
        ...

    async def on_message(self, handler: MessageHandler) -> None:
        """Register an async *handler* called for every inbound message."""
        ...

    async def start(self) -> None:
        """Connect / start listening.  Idempotent."""
        ...

    async def stop(self) -> None:
        """Disconnect / stop listening.  Idempotent."""
        ...

    def is_active(self) -> bool:
        """Return True while the channel is connected and ready."""
        ...
