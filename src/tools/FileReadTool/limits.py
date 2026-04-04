"""
Read tool output limits.

Two caps apply to text reads:

  | limit         | default | checks                    | cost          | on overflow     |
  |---------------|---------|---------------------------|---------------|-----------------|
  | maxSizeBytes  | 256 KB  | TOTAL FILE SIZE (not out) | 1 stat        | throws pre-read |
  | maxTokens     | 25000   | actual output tokens      | API roundtrip | throws post-read|
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

DEFAULT_MAX_OUTPUT_TOKENS = 25000
MAX_OUTPUT_SIZE = 256 * 1024  # 256 KB


def _get_env_max_tokens() -> Optional[int]:
    """Env var override for max output tokens."""
    override = os.environ.get("JARVIS_FILE_READ_MAX_OUTPUT_TOKENS")
    if override:
        try:
            parsed = int(override)
            if parsed > 0:
                return parsed
        except ValueError:
            pass
    return None


@dataclass
class FileReadingLimits:
    max_tokens: int
    max_size_bytes: int
    include_max_size_in_prompt: Optional[bool] = None
    targeted_range_nudge: Optional[bool] = None


@lru_cache(maxsize=1)
def get_default_file_reading_limits() -> FileReadingLimits:
    """Default limits for Read tool.

    Precedence for maxTokens: env var > DEFAULT_MAX_OUTPUT_TOKENS.
    """
    env_max_tokens = _get_env_max_tokens()
    max_tokens = env_max_tokens if env_max_tokens is not None else DEFAULT_MAX_OUTPUT_TOKENS

    return FileReadingLimits(
        max_tokens=max_tokens,
        max_size_bytes=MAX_OUTPUT_SIZE,
    )
