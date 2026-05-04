"""BlackboardClient — typed read/write API over Redis.

Thin wrapper. Stores Pydantic models as JSON-encoded strings under
prefixed keys. The prefix lets us isolate test runs from production
state (each test fixture uses a unique prefix; production uses
default `jarvis`).

Key layout:
  <prefix>:screen:active        — most recent ScreenFact (TTL ~30s)
  <prefix>:tool:<call_id>       — one ToolResult per call_id (no TTL)
  <prefix>:tool:_index          — Redis Sorted Set, score=ts, member=call_id
                                  (used for recent_tools chronological lookup)
  <prefix>:intent:<turn_id>     — one Intent per turn (no TTL)
"""
from __future__ import annotations

import os
from typing import Optional

import redis

from .schema import Intent, ScreenFact, ToolResult


class BlackboardClient:
    """Singleton-friendly. Construct with a prefix to namespace keys.
    Reuses a single Redis connection per process (Redis library is
    thread-safe for our usage)."""

    DEFAULT_SCREEN_TTL = 30  # seconds — see spec §5.1

    def __init__(
        self,
        *,
        host: str = None,
        port: int = None,
        prefix: str = None,
    ) -> None:
        self._r = redis.Redis(
            host=host or os.environ.get("REDIS_HOST", "localhost"),
            port=port or int(os.environ.get("REDIS_PORT", "6379")),
            decode_responses=True,
        )
        self._prefix = prefix or os.environ.get("JARVIS_BLACKBOARD_PREFIX", "jarvis")

    # ── Screen ─────────────────────────────────────────────────────

    def _screen_key(self) -> str:
        return f"{self._prefix}:screen:active"

    def write_screen_fact(
        self, fact: ScreenFact, *, ttl_seconds: Optional[int] = None,
    ) -> None:
        ttl = ttl_seconds if ttl_seconds is not None else self.DEFAULT_SCREEN_TTL
        self._r.set(self._screen_key(), fact.model_dump_json(), ex=ttl)

    def read_screen(self) -> Optional[ScreenFact]:
        raw = self._r.get(self._screen_key())
        if raw is None:
            return None
        return ScreenFact.model_validate_json(raw)

    # ── Tool results ───────────────────────────────────────────────

    def _tool_key(self, call_id: str) -> str:
        return f"{self._prefix}:tool:{call_id}"

    def _tool_index_key(self) -> str:
        return f"{self._prefix}:tool:_index"

    def write_tool_result(self, result: ToolResult) -> None:
        self._r.set(self._tool_key(result.call_id), result.model_dump_json())
        self._r.zadd(self._tool_index_key(), {result.call_id: result.ts})

    def read_tool_result(self, call_id: str) -> Optional[ToolResult]:
        raw = self._r.get(self._tool_key(call_id))
        if raw is None:
            return None
        return ToolResult.model_validate_json(raw)

    def recent_tools(self, limit: int = 5) -> list[ToolResult]:
        """Return the most recent `limit` tool results, newest first."""
        ids = self._r.zrevrange(self._tool_index_key(), 0, limit - 1)
        results: list[ToolResult] = []
        for call_id in ids:
            r = self.read_tool_result(call_id)
            if r is not None:
                results.append(r)
        return results

    # ── Intent ─────────────────────────────────────────────────────

    def _intent_key(self, turn_id: str) -> str:
        return f"{self._prefix}:intent:{turn_id}"

    def write_intent(self, intent: Intent) -> None:
        self._r.set(self._intent_key(intent.turn_id), intent.model_dump_json())

    def read_intent(self, turn_id: str) -> Optional[Intent]:
        raw = self._r.get(self._intent_key(turn_id))
        if raw is None:
            return None
        return Intent.model_validate_json(raw)
