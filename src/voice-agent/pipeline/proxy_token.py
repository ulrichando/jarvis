"""Mint HS256 proxy JWTs for the JARVIS LLM gateway (the "hub").

The hub (proxy.0wlan.com / localhost:4000) verifies bearer credentials
locally with a shared HMAC secret — see src/cli/src/proxy/proxyJwt.ts.
Required claims: aud="jarvis-proxy", iss="jarvis-web", non-empty sub,
valid exp. HS256 only. This module is the Python mirror of signProxyToken;
stdlib-only, no PyJWT dependency.

The secret comes from JARVIS_PROXY_JWT_SECRET (same env var the hub
itself reads). Tokens are minted fresh per agent job so a long-lived
worker never holds a stale credential.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time

PROXY_JWT_AUD = "jarvis-proxy"
PROXY_JWT_ISS = "jarvis-web"
DEFAULT_TTL_S = 24 * 3600  # per-job token; rooms don't live longer than this


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def mint_proxy_token(
    secret: str,
    *,
    sub: str = "voice-agent",
    ttl_s: int = DEFAULT_TTL_S,
    now_s: int | None = None,
) -> str:
    """Return a signed proxy JWT (header.payload.signature)."""
    if not secret:
        raise ValueError("empty JARVIS_PROXY_JWT_SECRET")
    now = int(time.time()) if now_s is None else now_s
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64url(
        json.dumps(
            {
                "sub": sub,
                "aud": PROXY_JWT_AUD,
                "iss": PROXY_JWT_ISS,
                "iat": now,
                "exp": now + ttl_s,
            }
        ).encode()
    )
    signing_input = f"{header}.{payload}"
    sig = _b64url(
        hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest()
    )
    return f"{signing_input}.{sig}"


def mint_from_env(*, sub: str = "voice-agent", ttl_s: int = DEFAULT_TTL_S) -> str:
    """Mint using JARVIS_PROXY_JWT_SECRET from the environment."""
    secret = os.environ.get("JARVIS_PROXY_JWT_SECRET", "")
    if not secret:
        raise RuntimeError(
            "JARVIS_PROXY_JWT_SECRET is not set — the voice agent cannot "
            "authenticate to the LLM gateway"
        )
    return mint_proxy_token(secret, sub=sub, ttl_s=ttl_s)
