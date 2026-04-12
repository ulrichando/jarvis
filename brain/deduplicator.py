"""
Request deduplicator — blocks identical requests from the same channel
within a configurable time window.

Prevents the voice channel from firing the same partial phrase 2-3 times
while the user is still mid-sentence.
"""

import hashlib
import time
import logging

logger = logging.getLogger(__name__)

# Dedup window per channel (seconds)
_WINDOW: dict[str, float] = {
    "voice":  3.0,   # voice can repeat very fast — 3 second suppression
    "chrome": 1.0,
    "cli":    0.5,
}

# Entries older than this are pruned on cleanup()
_MAX_AGE_SECONDS = 60.0


class RequestDeduplicator:
    """
    Blocks identical messages from the same channel within a rolling window.
    Thread-safe for asyncio (single-threaded event loop) — no lock needed.
    """

    def __init__(self) -> None:
        self._seen: dict[str, float] = {}  # hash → last_seen_timestamp

    def is_duplicate(self, channel_id: str, message: str) -> bool:
        """
        Return True if this exact (channel, message) was seen within the window.
        As a side effect, records the current timestamp for the key.
        """
        key    = self._make_key(channel_id, message)
        window = _WINDOW.get(channel_id, 1.0)
        now    = time.time()
        last   = self._seen.get(key)

        if last is not None and (now - last) < window:
            logger.debug(
                f"[dedup] blocked duplicate channel={channel_id} "
                f"window={window}s msg='{message[:50]}'"
            )
            return True

        self._seen[key] = now
        return False

    def cleanup(self) -> None:
        """Remove entries older than _MAX_AGE_SECONDS to prevent unbounded growth."""
        now = time.time()
        self._seen = {k: v for k, v in self._seen.items() if now - v < _MAX_AGE_SECONDS}

    @staticmethod
    def _make_key(channel_id: str, message: str) -> str:
        normalized = f"{channel_id}:{message.lower().strip()}"
        return hashlib.md5(normalized.encode()).hexdigest()


# Global singleton — import this in server.py
deduplicator = RequestDeduplicator()
