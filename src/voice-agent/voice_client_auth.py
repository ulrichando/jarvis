"""LiveKit auth + room-identity constants for the voice client.

Reads the SFU URL + API keypair + identity/room from env at import.
`mint_token()` builds a JWT for room-join — same shape as the
bridge's `/api/livekit/token` endpoint but done in-process so we
skip the HTTP round-trip (the API secret is already in env).

Hoisted from `jarvis_voice_client.py` 2026-05-10 (Step 7 of the
audit). Pure config + one function — no shared state, no I/O beyond
env-var reads at import.
"""
from __future__ import annotations

import logging
import os
import sys

from livekit import api


__all__ = [
    "URL",
    "API_KEY",
    "API_SECRET",
    "IDENTITY",
    "ROOM_NAME",
    "mint_token",
]


log = logging.getLogger("jarvis.voice_client")


URL: str        = os.environ.get("LIVEKIT_URL",        "ws://127.0.0.1:7880")
API_KEY: str    = os.environ.get("LIVEKIT_API_KEY",    "")
API_SECRET: str = os.environ.get("LIVEKIT_API_SECRET", "")
IDENTITY: str   = os.environ.get("JARVIS_VOICE_IDENTITY", "desktop-ulrich")
ROOM_NAME: str  = os.environ.get("JARVIS_VOICE_ROOM",     "jarvis")


def mint_token() -> str:
    """Mint a LiveKit JWT in-process.

    The bridge has a /api/livekit/token endpoint for the (now-shelved)
    webview client; here we already have the API secret in env, so we
    skip the HTTP round-trip.

    Hard-exits the process with code 2 if the keypair is missing —
    nothing to do without it.
    """
    if not API_KEY or not API_SECRET:
        log.error("LIVEKIT_API_KEY / LIVEKIT_API_SECRET not set — refusing to start")
        sys.exit(2)
    return (
        api.AccessToken(API_KEY, API_SECRET)
        .with_identity(IDENTITY)
        .with_name("Ulrich (desktop)")
        .with_grants(api.VideoGrants(
            room_join=True,
            room=ROOM_NAME,
            can_publish=True,
            can_subscribe=True,
        ))
        .to_jwt()
    )
