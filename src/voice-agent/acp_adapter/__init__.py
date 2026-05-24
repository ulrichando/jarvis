"""Agent Client Protocol (ACP) adapter for the JARVIS voice agent.

Exposes JARVIS's supervisor + tool registry to any ACP-compatible IDE
(Zed today; Cursor / VS Code / JetBrains as their ACP clients ship).
The adapter is a stdio JSON-RPC server: an IDE spawns ``bin/jarvis-acp``
as a subprocess and talks ACP over its stdin/stdout. Logging goes to
stderr so the protocol channel stays clean.

ACP is a peer surface — it does NOT spawn or depend on the LiveKit voice
worker. The two run side-by-side and share the same on-disk registry of
tools, memory, and skills.
"""

from .server import JarvisACPAgent

__all__ = ["JarvisACPAgent"]
