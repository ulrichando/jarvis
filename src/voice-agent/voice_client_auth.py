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

import datetime
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
    "SCREEN_SHARE_IDENTITY",
    "mint_token",
    "mint_screen_share_token",
]


log = logging.getLogger("jarvis.voice_client")


def _load_user_keys_env() -> None:
    """Load repo-root .env then ~/.jarvis/keys.env into os.environ.

    Priority chain (lowest → highest):
      1) Repo-root .env — centralized LLM provider keys
         (consolidated 2026-05-15; was duplicated across subproject .env files).
      2) ~/.jarvis/keys.env — user override; always wins.

    Same shape as `jarvis_agent._load_user_keys_env`. CRITICAL for the
    voice-client process: secrets (LIVEKIT_API_KEY / LIVEKIT_API_SECRET
    / GOOGLE_API_KEY) migrated to ~/.jarvis/keys.env 2026-05-10. The
    voice-agent process loads them at import via its own copy of this
    function; the voice-client process (separate systemd unit, separate
    Python interpreter) didn't — which left mint_token() hitting its
    sys.exit(2) "API key not set" guard on every start, putting the
    unit in a fast restart loop and the tray red. Loading happens
    BEFORE the API_KEY / API_SECRET reads below, so the env-driven
    module constants pick up the real values.

    Missing file at either layer is fine — graceful no-op. Failures
    parsing a line are swallowed at WARNING level.
    """

    def _parse(p: Path) -> None:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k and v:
                os.environ[k] = v

    # parents[2] = src/voice-agent → src → repo root
    sources = [
        Path(__file__).resolve().parents[2] / ".env",
        Path.home() / ".jarvis" / "keys.env",
    ]
    for src in sources:
        if not src.exists():
            continue
        try:
            _parse(src)
        except Exception as _e:
            log.warning(f"[env-load] {src.name} parse failed (non-fatal): {_e}")


_load_user_keys_env()


URL: str        = os.environ.get("LIVEKIT_URL",        "ws://127.0.0.1:7880")
API_KEY: str    = os.environ.get("LIVEKIT_API_KEY",    "")
API_SECRET: str = os.environ.get("LIVEKIT_API_SECRET", "")
IDENTITY: str   = os.environ.get("JARVIS_VOICE_IDENTITY", "desktop-ulrich")
ROOM_NAME: str  = os.environ.get("JARVIS_VOICE_ROOM",     "jarvis")

# JWT TTL — how long the token is valid before the SFU rejects it.
# Default 24h. Live failure 2026-05-11 15:26-15:35 UTC: voice-client
# minted a token, ran for 2h 2min, then the SFU returned 401
# "validation failed, token is expired (exp)" on every internal
# reconnect attempt. The LiveKit Rust engine's restart-connection
# logic reuses the original token — when that token expires mid-
# session, the inner reconnect loop spirals into 401s, the asyncio
# loop wedges, the watchdog kills the process, and the user hears
# nothing for ~2 minutes while systemd waits to restart.
# Setting TTL to 24h means a single voice-client process can sustain
# an all-day session without any token-driven reconnect failures.
# The LiveKit SDK default is 6h but the local livekit-server appears
# to cap at ~2h regardless (the empirical failure window). Override
# via JARVIS_VOICE_TOKEN_TTL_HOURS if you need to test against a
# different server policy.
TOKEN_TTL_HOURS: float = float(
    os.environ.get("JARVIS_VOICE_TOKEN_TTL_HOURS", "24")
)


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
        .with_ttl(datetime.timedelta(hours=TOKEN_TTL_HOURS))
        .with_grants(api.VideoGrants(
            room_join=True,
            room=ROOM_NAME,
            can_publish=True,
            can_subscribe=True,
        ))
        .to_jwt()
    )


# Identity used by the Tauri webview when it joins the LiveKit room
# for screen-share publishing (via the JS SDK's
# setScreenShareEnabled(true), which triggers the native OS picker).
# Distinct from the voice-client's IDENTITY so the two clients don't
# collide on the same identity in the room — LiveKit kicks the
# previous holder when an identity is re-used.
SCREEN_SHARE_IDENTITY: str = os.environ.get(
    "JARVIS_SCREEN_SHARE_IDENTITY", "desktop-ulrich-screen"
)


def mint_screen_share_token() -> str:
    """Mint a LiveKit JWT scoped to screen-share publishing only.

    Used by the Tauri webview when the user clicks the "Share Screen"
    button — the webview connects to the same room as the voice-client
    but with a DIFFERENT identity, then calls
    `room.localParticipant.setScreenShareEnabled(true)` to trigger the
    OS's native screen-picker (xdg-desktop-portal on Linux). The agent
    subscribes to any SOURCE_SCREENSHARE track in the room, so the
    screen-share observer sees the new track immediately.

    Same TTL + room as the main token; only the identity differs.
    can_subscribe=False because the webview doesn't need to consume
    media (the voice-client owns audio in/out for the user).
    """
    if not API_KEY or not API_SECRET:
        raise RuntimeError(
            "LIVEKIT_API_KEY / LIVEKIT_API_SECRET not set"
        )
    return (
        api.AccessToken(API_KEY, API_SECRET)
        .with_identity(SCREEN_SHARE_IDENTITY)
        .with_name("Ulrich (desktop screen)")
        .with_ttl(datetime.timedelta(hours=TOKEN_TTL_HOURS))
        .with_grants(api.VideoGrants(
            room_join=True,
            room=ROOM_NAME,
            can_publish=True,
            can_subscribe=False,
        ))
        .to_jwt()
    )
