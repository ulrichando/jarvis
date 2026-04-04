"""
Constants for timeout values used in bash operations.
"""

from __future__ import annotations

import os
from typing import Optional

DEFAULT_TIMEOUT_MS = 120_000  # 2 minutes
MAX_TIMEOUT_MS = 600_000  # 10 minutes


def get_default_bash_timeout_ms(env: Optional[dict[str, str]] = None) -> int:
    """
    Get the default timeout for bash operations in milliseconds.
    Checks BASH_DEFAULT_TIMEOUT_MS environment variable or returns 2 minutes.
    """
    if env is None:
        env_value = os.environ.get("BASH_DEFAULT_TIMEOUT_MS")
    else:
        env_value = env.get("BASH_DEFAULT_TIMEOUT_MS")

    if env_value:
        try:
            parsed = int(env_value)
            if parsed > 0:
                return parsed
        except ValueError:
            pass
    return DEFAULT_TIMEOUT_MS


def get_max_bash_timeout_ms(env: Optional[dict[str, str]] = None) -> int:
    """
    Get the maximum timeout for bash operations in milliseconds.
    Checks BASH_MAX_TIMEOUT_MS environment variable or returns 10 minutes.
    """
    if env is None:
        env_value = os.environ.get("BASH_MAX_TIMEOUT_MS")
    else:
        env_value = env.get("BASH_MAX_TIMEOUT_MS")

    default_timeout = get_default_bash_timeout_ms(env)

    if env_value:
        try:
            parsed = int(env_value)
            if parsed > 0:
                return max(parsed, default_timeout)
        except ValueError:
            pass
    return max(MAX_TIMEOUT_MS, default_timeout)
