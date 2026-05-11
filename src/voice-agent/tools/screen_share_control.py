"""Supervisor tool for toggling screen-share via voice command.

Wraps the voice-client's existing `POST :8767/screen-share` endpoint
so the user can say "Jarvis, share my screen" / "stop sharing" /
"start screen share" without reaching for the tray icon.

The tool POSTs with `ack: false` to suppress the voice-client's
built-in data-channel "Screen sharing on." TTS message — the
supervisor will compose its own one-line ack from the tool result.
Without that, the user would hear two acks back-to-back (one from
the voice-client's data publish, one from the supervisor's reply
to the tool result).

Added 2026-05-11 evening at user request — completes the screen-
share UX: voice-to-start, then ask about content. Pairs with the
screen_share Live subagent (subagents/screen_share.py) which
takes over for content questions once the share is active.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os

from livekit.agents.llm import function_tool


logger = logging.getLogger("jarvis.screen_share_control")


# Voice-client HTTP API address. Defaults to the standard local port
# the unit file binds to; override via env for non-default deployments.
_VOICE_CLIENT_URL: str = os.environ.get(
    "JARVIS_VOICE_CLIENT_URL", "http://127.0.0.1:8767"
)


@function_tool
async def set_screen_share(start: bool) -> str:
    """Turn the user's screen-share on or off via voice command.

    Use this when the user explicitly asks to share or stop sharing:
      - "share my screen" / "start screen share" / "Jarvis, share screen"
        → set_screen_share(start=True)
      - "stop sharing" / "stop screen share" / "stop the screen share"
        → set_screen_share(start=False)

    Once sharing is on, the user can ask "what's on my screen?" and
    the screen_share subagent will answer with real-time vision.
    Sharing is off by default on every fresh process — desktop
    capture is opt-in for privacy.

    Args:
        start: True to start sharing, False to stop.

    Returns the new state on success, or a one-line error for the
    supervisor to relay if the voice-client isn't reachable.
    """
    # aiohttp is already in the voice-client dep tree; lazy-import so
    # the tool module stays cheap to load.
    try:
        import aiohttp
    except Exception as e:
        return f"(screen-share control unavailable: {e})"

    payload = {"start": bool(start), "ack": False}
    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.post(
                f"{_VOICE_CLIENT_URL}/screen-share",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=5.0),
            ) as resp:
                body_text = await resp.text()
                if resp.status == 200:
                    try:
                        data = json.loads(body_text)
                    except Exception:
                        data = {}
                    sharing = bool(data.get("sharing"))
                    return "screen sharing started" if sharing else "screen sharing stopped"
                return (
                    f"(screen-share toggle failed: HTTP {resp.status} — "
                    f"{body_text[:120]})"
                )
    except asyncio.TimeoutError:
        return "(screen-share toggle timed out — voice-client may be wedged)"
    except aiohttp.ClientConnectorError:
        return "(voice-client unreachable on :8767)"
    except Exception as e:
        return f"(screen-share toggle errored: {type(e).__name__}: {e})"
