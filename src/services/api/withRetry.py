"""Retry logic for API calls with exponential backoff."""

from __future__ import annotations

import asyncio
import logging
import math
import os
import random
import re
from dataclasses import dataclass
from typing import Any, AsyncGenerator, Callable, Optional, TypeVar

logger = logging.getLogger(__name__)

DEFAULT_MAX_RETRIES = 10
FLOOR_OUTPUT_TOKENS = 3000
MAX_529_RETRIES = 3
BASE_DELAY_MS = 500

REPEATED_529_ERROR_MESSAGE = (
    "The API is currently overloaded. Please try again later."
)

T = TypeVar("T")


@dataclass
class RetryContext:
    max_tokens_override: Optional[int] = None
    model: str = ""
    fast_mode: bool = False


class CannotRetryError(Exception):
    def __init__(self, original_error: Exception, retry_context: RetryContext):
        self.original_error = original_error
        self.retry_context = retry_context
        super().__init__(str(original_error))


class FallbackTriggeredError(Exception):
    def __init__(self, original_model: str, fallback_model: str):
        self.original_model = original_model
        self.fallback_model = fallback_model
        super().__init__(f"Model fallback: {original_model} -> {fallback_model}")


def get_retry_delay(
    attempt: int,
    retry_after_header: Optional[str] = None,
    max_delay_ms: int = 32000,
) -> float:
    """Calculate retry delay with exponential backoff and jitter."""
    if retry_after_header:
        try:
            seconds = int(retry_after_header)
            return seconds * 1000
        except ValueError:
            pass

    base_delay = min(BASE_DELAY_MS * (2 ** (attempt - 1)), max_delay_ms)
    jitter = random.random() * 0.25 * base_delay
    return base_delay + jitter


def get_default_max_retries() -> int:
    env_val = os.environ.get("CLAUDE_CODE_MAX_RETRIES")
    if env_val:
        try:
            return int(env_val)
        except ValueError:
            pass
    return DEFAULT_MAX_RETRIES


def is_529_error(error: Exception) -> bool:
    """Check if an error is a 529 overloaded error."""
    status = getattr(error, "status", getattr(error, "status_code", None))
    if status == 529:
        return True
    if hasattr(error, "message") and '"type":"overloaded_error"' in str(error):
        return True
    return False


def should_retry(error: Exception) -> bool:
    """Determine if an error should be retried."""
    status = getattr(error, "status", getattr(error, "status_code", None))
    if status is None:
        return isinstance(error, (ConnectionError, TimeoutError))
    if status == 408:
        return True
    if status == 409:
        return True
    if status == 429:
        return True
    if status >= 500:
        return True
    return False


async def with_retry(
    operation: Callable[..., Any],
    max_retries: int = DEFAULT_MAX_RETRIES,
    model: str = "",
    signal: Optional[Any] = None,
) -> Any:
    """Execute an operation with retry logic."""
    retry_context = RetryContext(model=model)
    last_error: Optional[Exception] = None

    for attempt in range(1, max_retries + 2):
        try:
            return await operation(attempt, retry_context)
        except Exception as error:
            last_error = error
            logger.debug(f"API error (attempt {attempt}/{max_retries + 1}): {error}")

            if attempt > max_retries:
                raise CannotRetryError(error, retry_context) from error

            if not should_retry(error):
                raise CannotRetryError(error, retry_context) from error

            delay_ms = get_retry_delay(attempt)
            await asyncio.sleep(delay_ms / 1000)

    raise CannotRetryError(last_error or Exception("Unknown error"), retry_context)
