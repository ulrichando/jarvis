"""Auth file descriptor utilities for CCR token management."""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

CCR_TOKEN_DIR = "/home/jarvis/.jarvis/remote"
CCR_OAUTH_TOKEN_PATH = f"{CCR_TOKEN_DIR}/.oauth_token"
CCR_API_KEY_PATH = f"{CCR_TOKEN_DIR}/.api_key"
CCR_SESSION_INGRESS_TOKEN_PATH = f"{CCR_TOKEN_DIR}/.session_ingress_token"

# Cached values
_oauth_token_cache: Optional[str] = None
_api_key_cache: Optional[str] = None
_cache_initialized = {"oauth": False, "apikey": False}


def maybe_persist_token_for_subprocesses(
    path: str, token: str, token_name: str
) -> None:
    """Best-effort write of the token to a well-known location."""
    if not os.environ.get("CLAUDE_CODE_REMOTE"):
        return
    try:
        os.makedirs(CCR_TOKEN_DIR, mode=0o700, exist_ok=True)
        with open(path, "w") as f:
            f.write(token)
        os.chmod(path, 0o600)
        logger.debug(f"Persisted {token_name} to {path}")
    except Exception as e:
        logger.debug(f"Failed to persist {token_name}: {e}")


def read_token_from_well_known_file(
    path: str, token_name: str
) -> Optional[str]:
    """Fallback read from a well-known file."""
    try:
        with open(path) as f:
            token = f.read().strip()
        if not token:
            return None
        logger.debug(f"Read {token_name} from {path}")
        return token
    except FileNotFoundError:
        return None
    except Exception as e:
        logger.debug(f"Failed to read {token_name} from {path}: {e}")
        return None


def get_oauth_token_from_file_descriptor() -> Optional[str]:
    """Get the CCR-injected OAuth token."""
    global _oauth_token_cache
    if _cache_initialized["oauth"]:
        return _oauth_token_cache

    _cache_initialized["oauth"] = True
    token = read_token_from_well_known_file(CCR_OAUTH_TOKEN_PATH, "OAuth token")
    _oauth_token_cache = token
    return token


def get_api_key_from_file_descriptor() -> Optional[str]:
    """Get the CCR-injected API key."""
    global _api_key_cache
    if _cache_initialized["apikey"]:
        return _api_key_cache

    _cache_initialized["apikey"] = True
    token = read_token_from_well_known_file(CCR_API_KEY_PATH, "API key")
    _api_key_cache = token
    return token
