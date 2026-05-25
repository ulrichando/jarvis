"""Shared helpers for direct xAI HTTP integrations (JARVIS voice-agent).

JARVIS-native, env-only credential resolution for xAI's HTTP API. Reads
``XAI_API_KEY`` / ``XAI_BASE_URL`` from the process environment (which the
voice-agent populates from ``src/voice-agent/.env`` at startup). No OAuth
token store and no external config layer — a bare API key is the only
credential path JARVIS supports for xAI.

Kept stdlib-only and import-safe at module scope so tool modules can import it
at load time during the registry walk.

Ported from the upstream xai_http helper; the OAuth / config-store resolution
was dropped in favor of plain env vars. No upstream brand tokens.
"""
from __future__ import annotations

import os
from typing import Dict

__all__ = [
    "has_xai_credentials",
    "xai_user_agent",
    "resolve_xai_http_credentials",
]

# Default xAI API base; overridable via XAI_BASE_URL.
_DEFAULT_XAI_BASE_URL = "https://api.x.ai/v1"


def has_xai_credentials() -> bool:
    """Cheap probe — True when ``XAI_API_KEY`` is set and non-empty.

    Deliberately env-only and free of disk/network access so it is safe to
    call from hot paths (tool-registration scans, ``check_fn`` probes).
    """
    return bool(os.environ.get("XAI_API_KEY", "").strip())


def xai_user_agent() -> str:
    """Return a stable JARVIS-specific User-Agent for xAI HTTP calls."""
    return "jarvis-voice-agent-xai/1.0"


def resolve_xai_http_credentials(*, force_refresh: bool = False) -> Dict[str, str]:
    """Resolve bearer credentials for direct xAI HTTP endpoints.

    Reads ``XAI_API_KEY`` and ``XAI_BASE_URL`` from the environment. The
    ``force_refresh`` argument is accepted-and-ignored — there is no token
    refresh in the env-only model; it exists so call sites ported from the
    upstream OAuth-aware helper keep compiling.

    Returns a dict with keys ``provider`` (always ``"xai"``), ``api_key``
    (may be empty when unconfigured), and ``base_url``.
    """
    api_key = os.environ.get("XAI_API_KEY", "").strip()
    base_url = (os.environ.get("XAI_BASE_URL", "").strip() or _DEFAULT_XAI_BASE_URL).rstrip("/")
    return {
        "provider": "xai",
        "api_key": api_key,
        "base_url": base_url,
    }
