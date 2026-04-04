"""JWT utilities and token refresh scheduling for bridge sessions."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional, Union

logger = logging.getLogger(__name__)

TOKEN_REFRESH_BUFFER_MS = 5 * 60 * 1000
FALLBACK_REFRESH_INTERVAL_MS = 30 * 60 * 1000
MAX_REFRESH_FAILURES = 3
REFRESH_RETRY_DELAY_MS = 60_000


def _format_duration(ms: float) -> str:
    if ms < 60_000:
        return f"{round(ms / 1000)}s"
    m = int(ms // 60_000)
    s = round((ms % 60_000) / 1000)
    return f"{m}m {s}s" if s > 0 else f"{m}m"


def decode_jwt_payload(token: str) -> Any:
    """Decode a JWT's payload segment without verifying the signature."""
    jwt = token[len("sk-ant-si-"):] if token.startswith("sk-ant-si-") else token
    parts = jwt.split(".")
    if len(parts) != 3 or not parts[1]:
        return None
    try:
        padded = parts[1] + "=" * (4 - len(parts[1]) % 4)
        decoded = base64.urlsafe_b64decode(padded).decode("utf-8")
        return json.loads(decoded)
    except Exception:
        return None


def decode_jwt_expiry(token: str) -> Optional[int]:
    """Decode the `exp` claim from a JWT without verifying the signature."""
    payload = decode_jwt_payload(token)
    if isinstance(payload, dict) and isinstance(payload.get("exp"), (int, float)):
        return int(payload["exp"])
    return None


def create_token_refresh_scheduler(
    get_access_token: Callable[[], Union[Optional[str], Awaitable[Optional[str]]]],
    on_refresh: Callable[[str, str], None],
    label: str,
    refresh_buffer_ms: int = TOKEN_REFRESH_BUFFER_MS,
):
    """Creates a token refresh scheduler that proactively refreshes session tokens."""
    timers: dict[str, asyncio.TimerHandle] = {}
    failure_counts: dict[str, int] = {}
    generations: dict[str, int] = {}

    def _next_generation(session_id: str) -> int:
        gen = generations.get(session_id, 0) + 1
        generations[session_id] = gen
        return gen

    def schedule(session_id: str, token: str) -> None:
        expiry = decode_jwt_expiry(token)
        if not expiry:
            logger.debug(
                "[%s:token] Could not decode JWT expiry for sessionId=%s",
                label, session_id,
            )
            return

        existing = timers.pop(session_id, None)
        if existing:
            existing.cancel()

        gen = _next_generation(session_id)
        delay_ms = expiry * 1000 - time.time() * 1000 - refresh_buffer_ms

        if delay_ms <= 0:
            logger.debug("[%s:token] Token expired, refreshing immediately", label)
            asyncio.get_event_loop().call_soon(
                lambda: asyncio.ensure_future(_do_refresh(session_id, gen))
            )
            return

        logger.debug(
            "[%s:token] Scheduled token refresh in %s",
            label, _format_duration(delay_ms),
        )
        loop = asyncio.get_event_loop()
        handle = loop.call_later(
            delay_ms / 1000,
            lambda: asyncio.ensure_future(_do_refresh(session_id, gen)),
        )
        timers[session_id] = handle

    def schedule_from_expires_in(session_id: str, expires_in_seconds: int) -> None:
        existing = timers.pop(session_id, None)
        if existing:
            existing.cancel()
        gen = _next_generation(session_id)
        delay_ms = max(expires_in_seconds * 1000 - refresh_buffer_ms, 30_000)
        logger.debug(
            "[%s:token] Scheduled token refresh in %s",
            label, _format_duration(delay_ms),
        )
        loop = asyncio.get_event_loop()
        handle = loop.call_later(
            delay_ms / 1000,
            lambda: asyncio.ensure_future(_do_refresh(session_id, gen)),
        )
        timers[session_id] = handle

    async def _do_refresh(session_id: str, gen: int) -> None:
        try:
            result = get_access_token()
            if asyncio.iscoroutine(result) or asyncio.isfuture(result):
                oauth_token = await result
            else:
                oauth_token = result
        except Exception as err:
            logger.error("[%s:token] getAccessToken error: %s", label, err)
            oauth_token = None

        if generations.get(session_id) != gen:
            return

        if not oauth_token:
            failures = failure_counts.get(session_id, 0) + 1
            failure_counts[session_id] = failures
            if failures < MAX_REFRESH_FAILURES:
                loop = asyncio.get_event_loop()
                handle = loop.call_later(
                    REFRESH_RETRY_DELAY_MS / 1000,
                    lambda: asyncio.ensure_future(_do_refresh(session_id, gen)),
                )
                timers[session_id] = handle
            return

        failure_counts.pop(session_id, None)
        on_refresh(session_id, oauth_token)

        loop = asyncio.get_event_loop()
        handle = loop.call_later(
            FALLBACK_REFRESH_INTERVAL_MS / 1000,
            lambda: asyncio.ensure_future(_do_refresh(session_id, gen)),
        )
        timers[session_id] = handle

    def cancel(session_id: str) -> None:
        _next_generation(session_id)
        handle = timers.pop(session_id, None)
        if handle:
            handle.cancel()
        failure_counts.pop(session_id, None)

    def cancel_all() -> None:
        for sid in list(generations.keys()):
            _next_generation(sid)
        for handle in timers.values():
            handle.cancel()
        timers.clear()
        failure_counts.clear()

    return type("TokenRefreshScheduler", (), {
        "schedule": staticmethod(schedule),
        "schedule_from_expires_in": staticmethod(schedule_from_expires_in),
        "cancel": staticmethod(cancel),
        "cancel_all": staticmethod(cancel_all),
    })()
