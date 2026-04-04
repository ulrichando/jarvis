"""Work secret decoding and SDK URL building."""

from __future__ import annotations

import base64
import json
import logging
from typing import Any, Optional

import aiohttp

logger = logging.getLogger(__name__)


def decode_work_secret(secret: str) -> dict[str, Any]:
    """Decode a base64url-encoded work secret and validate its version."""
    raw = base64.urlsafe_b64decode(secret + "==").decode("utf-8")
    parsed = json.loads(raw)
    if not isinstance(parsed, dict) or parsed.get("version") != 1:
        version = parsed.get("version", "unknown") if isinstance(parsed, dict) else "unknown"
        raise ValueError(f"Unsupported work secret version: {version}")
    if not isinstance(parsed.get("session_ingress_token"), str) or not parsed["session_ingress_token"]:
        raise ValueError("Invalid work secret: missing or empty session_ingress_token")
    if not isinstance(parsed.get("api_base_url"), str):
        raise ValueError("Invalid work secret: missing api_base_url")
    return parsed


def build_sdk_url(api_base_url: str, session_id: str) -> str:
    """Build a WebSocket SDK URL from the API base URL and session ID."""
    is_localhost = "localhost" in api_base_url or "127.0.0.1" in api_base_url
    protocol = "ws" if is_localhost else "wss"
    version = "v2" if is_localhost else "v1"
    host = api_base_url.replace("https://", "").replace("http://", "").rstrip("/")
    return f"{protocol}://{host}/{version}/session_ingress/ws/{session_id}"


def same_session_id(a: str, b: str) -> bool:
    """Compare two session IDs regardless of their tagged-ID prefix."""
    if a == b:
        return True
    a_body = a[a.rfind("_") + 1:]
    b_body = b[b.rfind("_") + 1:]
    return len(a_body) >= 4 and a_body == b_body


def build_ccr_v2_sdk_url(api_base_url: str, session_id: str) -> str:
    """Build a CCR v2 session URL."""
    base = api_base_url.rstrip("/")
    return f"{base}/v1/code/sessions/{session_id}"


async def register_worker(session_url: str, access_token: str) -> int:
    """Register this bridge as the worker for a CCR v2 session."""
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{session_url}/worker/register",
            json={},
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                "anthropic-version": "2023-06-01",
            },
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            data = await resp.json()
            raw = data.get("worker_epoch")
            epoch = int(raw) if isinstance(raw, str) else raw
            if not isinstance(epoch, int):
                raise ValueError(f"registerWorker: invalid worker_epoch in response: {json.dumps(data)}")
            return epoch
