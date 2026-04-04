"""Transport utilities shared across transport implementations."""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


def parse_sse_line(line: str) -> Optional[dict[str, Any]]:
    """Parse an SSE data line into a dict."""
    if not line.startswith("data: "):
        return None
    try:
        return json.loads(line[6:])
    except json.JSONDecodeError:
        return None


def build_auth_headers(access_token: str) -> dict[str, str]:
    """Build authorization headers for transport connections."""
    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
