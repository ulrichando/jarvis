"""OAuth client -- converted from TypeScript."""
from __future__ import annotations
from typing import Any, Dict, Optional


def is_oauth_token_expired(expires_at: float) -> bool:
    """Check if an OAuth token is expired."""
    import time
    return time.time() >= expires_at
