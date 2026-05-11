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
from pathlib import Path

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


def _load_user_keys_env() -> None:
    """Load ~/.jarvis/keys.env into os.environ (override semantics).

    Same shape as `jarvis_agent._load_user_keys_env`. CRITICAL for the
    voice-client process: secrets (LIVEKIT_API_KEY / LIVEKIT_API_SECRET
    / GOOGLE_API_KEY) migrated to ~/.jarvis/keys.env 2026-05-10. The
    voice-agent process loads them at import via its own copy of this
    function; the voice-client process (separate systemd unit, separate
    Python interpreter) didn't — which left mint_token() hitting its
    sys.exit(2) "API key not set" guard on every start, putting the
    unit in a fast restart loop and the tray red. Loading the keys
    file here happens BEFORE the API_KEY / API_SECRET reads below,
    so the env-driven module constants pick up the real values.

    Missing file is fine — graceful no-op. Failures parsing a line are
    swallowed at WARNING level.
    """
    p = Path.home() / ".jarvis" / "keys.env"
    if not p.exists():
        return
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k and v:
                os.environ[k] = v   # override repo .env (matches voice-agent semantics)
    except Exception as _e:
        log.warning(f"[keys.env] load failed (non-fatal): {_e}")


_load_user_keys_env()


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
