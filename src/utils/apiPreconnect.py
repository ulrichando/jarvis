"""
Preconnect to the Anthropic API to overlap TCP+TLS handshake with startup.

Fires a HEAD request during init so the TLS handshake happens in parallel
with action-handler work.
"""

from __future__ import annotations

import os
from typing import Optional

import aiohttp

_fired = False


def _is_env_truthy(value: Optional[str]) -> bool:
    if value is None:
        return False
    return value.lower() in ("1", "true", "yes")


async def preconnect_anthropic_api() -> None:
    """Fire-and-forget HEAD request to warm up the connection pool."""
    global _fired
    if _fired:
        return
    _fired = True

    # Skip if using a cloud provider
    if any(
        _is_env_truthy(os.environ.get(k))
        for k in (
            "CLAUDE_CODE_USE_BEDROCK",
            "CLAUDE_CODE_USE_VERTEX",
            "CLAUDE_CODE_USE_FOUNDRY",
        )
    ):
        return

    # Skip if proxy/mTLS/unix
    skip_vars = (
        "HTTPS_PROXY",
        "https_proxy",
        "HTTP_PROXY",
        "http_proxy",
        "ANTHROPIC_UNIX_SOCKET",
        "CLAUDE_CODE_CLIENT_CERT",
        "CLAUDE_CODE_CLIENT_KEY",
    )
    if any(os.environ.get(v) for v in skip_vars):
        return

    base_url = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.head(base_url, timeout=aiohttp.ClientTimeout(total=10)):
                pass
    except Exception:
        pass
