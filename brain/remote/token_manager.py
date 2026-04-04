"""
JWT token management with proactive refresh scheduling.

Handles token lifecycle: expiry detection, automatic refresh scheduling,
and JWT payload decoding for session ingress tokens.
"""

import asyncio
import base64
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional

log = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Data classes
# ------------------------------------------------------------------

@dataclass
class TokenInfo:
    """Holds a token and its metadata."""
    token: str
    expires_at: float = 0.0  # Unix timestamp
    refresh_token: str = ""
    token_type: str = "bearer"  # bearer, session_ingress


# ------------------------------------------------------------------
# JWT utilities
# ------------------------------------------------------------------

# Known prefixes on tokens that precede the actual JWT
_TOKEN_PREFIXES = ("sk-ant-si-",)


def decode_jwt_expiry(token: str) -> Optional[float]:
    """Decode a JWT payload (no signature verification) and extract the exp claim.

    Strips common prefixes (e.g. "sk-ant-si-") before decoding.
    Returns the Unix timestamp from the ``exp`` claim, or None if
    the claim is missing or the token cannot be decoded.
    """
    if not token:
        return None

    jwt_str = token
    for prefix in _TOKEN_PREFIXES:
        if jwt_str.startswith(prefix):
            jwt_str = jwt_str[len(prefix):]
            break

    parts = jwt_str.split(".")
    if len(parts) < 2:
        return None

    payload_b64 = parts[1]
    # Add padding if needed
    padding = 4 - len(payload_b64) % 4
    if padding != 4:
        payload_b64 += "=" * padding

    try:
        # base64url -> standard base64
        payload_b64 = payload_b64.replace("-", "+").replace("_", "/")
        payload_bytes = base64.b64decode(payload_b64)
        payload = json.loads(payload_bytes)
    except Exception:
        log.debug("token_manager: failed to decode JWT payload")
        return None

    exp = payload.get("exp")
    if exp is not None:
        try:
            return float(exp)
        except (ValueError, TypeError):
            return None
    return None


def is_token_expired(token_info: TokenInfo, buffer_seconds: int = 0) -> bool:
    """Check whether a token is expired or will expire within buffer_seconds.

    Returns True if the token has no expiry set (expires_at == 0) and
    buffer_seconds > 0, or if the current time plus buffer exceeds expires_at.
    """
    if token_info.expires_at <= 0:
        # No expiry information — treat as not expired
        return False
    return time.time() + buffer_seconds >= token_info.expires_at


# ------------------------------------------------------------------
# Token refresh scheduler
# ------------------------------------------------------------------

class TokenRefreshScheduler:
    """Proactively schedules token refreshes before expiry.

    Maintains one async task per session that sleeps until the token
    is about to expire, then calls the refresh callback to obtain a
    fresh token and reschedules itself.
    """

    def __init__(
        self,
        refresh_callback: Callable,
        refresh_buffer_seconds: int = 300,
    ):
        self._refresh_callback = refresh_callback
        self._refresh_buffer = refresh_buffer_seconds
        self._scheduled_tasks: Dict[str, asyncio.Task] = {}
        self._generation: Dict[str, int] = {}
        self._failure_count: Dict[str, int] = {}
        self._max_failures: int = 3

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def schedule(self, session_id: str, token_info: TokenInfo):
        """Schedule a refresh for *session_id* based on token_info.expires_at.

        Cancels any previously scheduled task for this session and
        increments the generation counter so stale tasks bail out.
        """
        # Cancel existing
        self.cancel(session_id)

        # Bump generation
        gen = self._generation.get(session_id, 0) + 1
        self._generation[session_id] = gen

        if token_info.expires_at <= 0:
            log.debug("token_manager: no expiry for session %s, skipping schedule", session_id)
            return

        delay = token_info.expires_at - self._refresh_buffer - time.time()
        if delay < 0:
            delay = 0  # Refresh immediately

        log.debug(
            "token_manager: scheduling refresh for session %s in %.1fs (gen=%d)",
            session_id, delay, gen,
        )

        task = asyncio.ensure_future(self._refresh_task(session_id, delay, gen))
        self._scheduled_tasks[session_id] = task

    def schedule_from_expires_in(self, session_id: str, token: str, expires_in: int):
        """Convenience: schedule a refresh from an expires_in value (seconds from now)."""
        expires_at = time.time() + expires_in
        info = TokenInfo(token=token, expires_at=expires_at)
        self.schedule(session_id, info)

    def cancel(self, session_id: str):
        """Cancel any scheduled refresh for *session_id*."""
        task = self._scheduled_tasks.pop(session_id, None)
        if task and not task.done():
            task.cancel()

    def cancel_all(self):
        """Cancel all scheduled refresh tasks."""
        for sid in list(self._scheduled_tasks):
            self.cancel(sid)
        self._scheduled_tasks.clear()
        self._generation.clear()
        self._failure_count.clear()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _refresh_task(self, session_id: str, delay: float, generation: int):
        """Sleep, then refresh the token for *session_id*."""
        try:
            if delay > 0:
                await asyncio.sleep(delay)

            # Stale check — a newer schedule may have superseded us
            if self._generation.get(session_id, 0) != generation:
                log.debug(
                    "token_manager: stale refresh task for session %s (gen %d != %d)",
                    session_id, generation, self._generation.get(session_id, 0),
                )
                return

            log.info("token_manager: refreshing token for session %s", session_id)

            try:
                new_token_info = await self._refresh_callback(session_id)
            except Exception as exc:
                log.error("token_manager: refresh callback failed for %s: %s", session_id, exc)
                new_token_info = None

            if new_token_info is not None:
                # Success — reset failures and schedule next refresh
                self._failure_count[session_id] = 0
                self.schedule(session_id, new_token_info)
            else:
                # Failure — retry with backoff if under threshold
                count = self._failure_count.get(session_id, 0) + 1
                self._failure_count[session_id] = count

                if count < self._max_failures:
                    log.warning(
                        "token_manager: refresh failed for %s (attempt %d/%d), retrying in 30s",
                        session_id, count, self._max_failures,
                    )
                    retry_gen = self._generation.get(session_id, 0) + 1
                    self._generation[session_id] = retry_gen
                    task = asyncio.ensure_future(
                        self._refresh_task(session_id, 30.0, retry_gen)
                    )
                    self._scheduled_tasks[session_id] = task
                else:
                    log.error(
                        "token_manager: refresh failed for %s after %d attempts, giving up",
                        session_id, count,
                    )

        except asyncio.CancelledError:
            log.debug("token_manager: refresh task cancelled for session %s", session_id)
        except Exception as exc:
            log.error("token_manager: unexpected error in refresh task for %s: %s", session_id, exc)


# ------------------------------------------------------------------
# Module-level singleton
# ------------------------------------------------------------------

_scheduler: Optional[TokenRefreshScheduler] = None


def get_token_scheduler(
    refresh_callback: Callable = None,
) -> TokenRefreshScheduler:
    """Return the module-level TokenRefreshScheduler singleton.

    On first call, *refresh_callback* must be provided to initialise
    the scheduler.  Subsequent calls return the existing instance
    (refresh_callback is ignored if already initialised).
    """
    global _scheduler
    if _scheduler is None:
        if refresh_callback is None:
            raise ValueError(
                "refresh_callback is required on first call to get_token_scheduler"
            )
        _scheduler = TokenRefreshScheduler(refresh_callback=refresh_callback)
    return _scheduler
