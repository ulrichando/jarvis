"""
In-memory response cache with per-tool TTL.
Prevents duplicate API calls for identical recent requests.
"""

import hashlib
import time
import logging
from typing import Any

logger = logging.getLogger(__name__)


class ResponseCache:
    """
    Keyed by MD5(channel_id + message). Each entry expires based on TTL.
    State-mutating tools (reminders, system control) are never cached.
    """

    # TTL in seconds per tool/category — 0 means never cache
    TTL: dict[str, int] = {
        "get_weather":    600,   # 10 minutes
        "web_search":     300,   # 5 minutes
        "play_music":     0,     # never cache — re-run each time
        "set_reminder":   0,     # never cache — mutates state
        "open_app":       0,     # never cache — mutates state
        "system_control": 0,     # never cache — mutates state
        "general":        120,   # 2 minutes for plain Qwen/Claude responses
    }

    def __init__(self) -> None:
        # key → (response, expires_at)
        self._store: dict[str, tuple[str, float]] = {}

    def get(self, key: str) -> str | None:
        """Return a cached response if it exists and hasn't expired, else None."""
        if key not in self._store:
            return None
        response, expires_at = self._store[key]
        if time.time() > expires_at:
            del self._store[key]
            logger.debug(f"[cache] expired key={key[:8]}...")
            return None
        logger.debug(f"[cache] hit key={key[:8]}...")
        return response

    def set(self, key: str, response: str, tool_name: str | None = None) -> None:
        """
        Cache a response with the appropriate TTL for the given tool.
        Does nothing if the tool's TTL is 0 (must not be cached).
        """
        ttl = self.TTL.get(tool_name or "general", 120)
        if ttl <= 0:
            return
        self._store[key] = (response, time.time() + ttl)
        logger.debug(f"[cache] set key={key[:8]}... ttl={ttl}s tool={tool_name}")

    def make_key(self, channel_id: str, message: str) -> str:
        """Generate a stable, deterministic cache key for a request."""
        normalized = f"{channel_id}:{message.lower().strip()}"
        return hashlib.md5(normalized.encode()).hexdigest()

    def invalidate(self, key: str) -> None:
        """Manually remove a single cache entry."""
        self._store.pop(key, None)

    def clear_all(self) -> None:
        """Flush the entire cache (e.g. on server restart or forced refresh)."""
        self._store.clear()
        logger.info("[cache] cleared all entries")
