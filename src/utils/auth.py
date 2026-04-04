"""Authentication utilities."""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_API_KEY_HELPER_TTL = 5 * 60  # 5 minutes


def is_anthropic_auth_enabled() -> bool:
    """Check if Anthropic auth (OAuth) is enabled."""
    is_3p = (
        os.environ.get("CLAUDE_CODE_USE_BEDROCK")
        or os.environ.get("CLAUDE_CODE_USE_VERTEX")
        or os.environ.get("CLAUDE_CODE_USE_FOUNDRY")
    )
    if is_3p:
        return False

    has_api_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    if has_api_key:
        return False

    return True


def get_auth_token_source() -> dict:
    """Get the current auth token source."""
    if os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return {"source": "ANTHROPIC_AUTH_TOKEN", "hasToken": True}
    if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        return {"source": "CLAUDE_CODE_OAUTH_TOKEN", "hasToken": True}
    if os.environ.get("ANTHROPIC_API_KEY"):
        return {"source": "ANTHROPIC_API_KEY", "hasToken": True}
    return {"source": "none", "hasToken": False}


def get_anthropic_api_key() -> Optional[str]:
    """Get the Anthropic API key from environment."""
    return os.environ.get("ANTHROPIC_API_KEY")


def is_api_subscriber() -> bool:
    """Check if the user is an API subscriber."""
    return bool(os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"))


def get_subscription_type() -> Optional[str]:
    """Get the subscription type."""
    return None
