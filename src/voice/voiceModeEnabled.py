"""
Voice mode feature gating and authentication checks.

Converted from voiceModeEnabled.ts to Python.
"""

from __future__ import annotations

from typing import Optional


def _get_feature_value_cached(flag: str, default: bool) -> bool:
    """
    Stub for GrowthBook feature flag lookup.
    Returns the default value when no GrowthBook integration is available.
    """
    return default


def _is_anthropic_auth_enabled() -> bool:
    """
    Stub for checking if Anthropic OAuth auth provider is configured.
    """
    return False


def _get_claude_ai_oauth_tokens() -> Optional[dict]:
    """
    Stub for retrieving Claude AI OAuth tokens from keychain/credential store.
    Returns dict with 'accessToken' key if available, or None.
    """
    return None


def is_voice_growthbook_enabled() -> bool:
    """
    Kill-switch check for voice mode. Returns True unless the
    tengu_amber_quartz_disabled GrowthBook flag is flipped on (emergency off).

    Default False means a missing/stale disk cache reads as "not killed" --
    so fresh installs get voice working immediately without waiting for
    GrowthBook init.

    Use this for deciding whether voice mode should be *visible*
    (e.g., command registration, config UI).
    """
    return not _get_feature_value_cached("tengu_amber_quartz_disabled", False)


def has_voice_auth() -> bool:
    """
    Auth-only check for voice mode. Returns True when the user has a valid
    Anthropic OAuth token.

    Voice mode requires Anthropic OAuth -- it uses the voice_stream endpoint
    on claude.ai which is not available with API keys, Bedrock, Vertex, or
    Foundry.
    """
    if not _is_anthropic_auth_enabled():
        return False

    tokens = _get_claude_ai_oauth_tokens()
    return bool(tokens and tokens.get("accessToken"))


def is_voice_mode_enabled() -> bool:
    """
    Full runtime check: auth + GrowthBook kill-switch.

    Callers: /voice command paths where a fresh keychain read is acceptable.
    For UI render paths, cache the auth result instead.
    """
    return has_voice_auth() and is_voice_growthbook_enabled()
