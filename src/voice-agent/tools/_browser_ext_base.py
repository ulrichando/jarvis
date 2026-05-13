"""Bridge POST + response-summarize primitives shared by every
`tools/browser_ext_*` module.

Every browser-extension @function_tool follows the same shape:

    @function_tool
    async def ext_thing(arg: str) -> str:
        return _summarize(await _post("thing", arg=arg))

This module owns `_post` (HTTP POST to the bridge → JSON response)
and `_summarize` (collapse the bridge's structured result to one LLM-
voiceable line). Plus the bridge URL / timeout / auth constants.

Hoisted from `tools/browser_ext.py` 2026-05-10 (Step 7 of the audit
— browser_ext regrouping). The previously-single 746-line file is
now split four ways by responsibility: nav/search, query/observe,
mouse+keyboard+scroll interaction, file+storage+power tools.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

import aiohttp


__all__ = [
    "BRIDGE_URL",
    "DEFAULT_TIMEOUT_MS",
    "LOCAL_TOKEN",
    "post",
    "summarize",
]


logger = logging.getLogger("jarvis.browser_ext")


BRIDGE_URL: str          = os.environ.get("JARVIS_BRIDGE_URL", "http://localhost:8765")
DEFAULT_TIMEOUT_MS: int  = int(os.environ.get("JARVIS_EXT_TIMEOUT_MS", "10000"))
# Optional bridge bearer token. Bridge enforces it when
# JARVIS_REQUIRE_LOCAL_AUTH=1; we always send it if available so the
# flag can be flipped without redeploying the agent.
LOCAL_TOKEN: str         = os.environ.get("JARVIS_LOCAL_API_TOKEN", "")


# ── Chrome auto-launch on "extension not connected" ──────────────
#
# Live failure 2026-05-13 01:38 UTC. The browser subagent re-activated
# WITHOUT going through `_transfer` (the subagent's `task_done` REFUSED
# path stays on the same agent instance — no re-handoff → no pre_transfer
# fires). The user had closed Chrome between the two voice attempts;
# pre_transfer's launch logic was never invoked; the subagent's ext_*
# calls hit a disconnected bridge and bailed.
#
# Move the launch responsibility from the handoff layer DOWN into the
# bridge POST itself: when bridge returns "extension not connected",
# spawn Chrome, wait for the extension to register, retry the POST
# once. Idempotent — Chrome that's already running is a no-op
# (`google-chrome --new-window` reuses the existing process).
_CHROME_LAUNCH_CMD = ["setsid", "-f", "google-chrome", "--profile-directory=Default"]
_AUTOLAUNCH_DISABLE = os.environ.get("JARVIS_BROWSER_AUTOLAUNCH_DISABLE", "0") == "1"
_AUTOLAUNCH_WAIT_S = float(os.environ.get("JARVIS_BROWSER_AUTOLAUNCH_WAIT_S", "8.0"))
_AUTOLAUNCH_POLL_S = float(os.environ.get("JARVIS_BROWSER_AUTOLAUNCH_POLL_S", "0.3"))


async def _ext_status_connected(http_session: aiohttp.ClientSession) -> bool:
    """GET /api/ext_status — True if the extension is connected to the
    bridge. False on any error (treated the same as disconnected)."""
    try:
        async with http_session.get(
            f"{BRIDGE_URL}/api/ext_status",
            timeout=aiohttp.ClientTimeout(total=2.0),
        ) as r:
            if r.status != 200:
                return False
            data = await r.json()
            return bool(data.get("connected"))
    except Exception:
        return False


async def _launch_chrome_and_wait(http_session: aiohttp.ClientSession) -> bool:
    """Fire `setsid -f google-chrome --profile-directory=Default`, then
    poll `/api/ext_status` for up to _AUTOLAUNCH_WAIT_S until the
    extension registers. Returns True if connected within budget."""
    logger.info("[browser-ext] auto-launching Chrome — extension was not connected")
    try:
        proc = await asyncio.create_subprocess_exec(
            *_CHROME_LAUNCH_CMD,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
        # setsid -f exits ~immediately after forking Chrome.
        await asyncio.wait_for(proc.wait(), timeout=2.0)
    except Exception as e:
        logger.warning(f"[browser-ext] Chrome launch failed: {e}")
        return False
    deadline = time.monotonic() + _AUTOLAUNCH_WAIT_S
    while time.monotonic() < deadline:
        await asyncio.sleep(_AUTOLAUNCH_POLL_S)
        if await _ext_status_connected(http_session):
            elapsed = _AUTOLAUNCH_WAIT_S - (deadline - time.monotonic())
            logger.info(f"[browser-ext] Chrome auto-launch connected after {elapsed:.1f}s")
            return True
    logger.warning(
        f"[browser-ext] Chrome auto-launch: extension never connected within "
        f"{_AUTOLAUNCH_WAIT_S}s"
    )
    return False


async def post(action: str, **args: Any) -> dict:
    """Post a command to the bridge. Returns the bridge's JSON response
    verbatim — usually `{ok: bool, ...}`. Network/extension errors
    surface as `{ok: False, error: "..."}` so the LLM gets actionable
    text rather than a Python exception.

    On "extension not connected" responses (503), this helper auto-
    launches Chrome via `setsid -f google-chrome` and retries the
    POST ONCE. Opt out with `JARVIS_BROWSER_AUTOLAUNCH_DISABLE=1`.
    """
    # Pydantic v2.10+ rejects leading-underscore field names in
    # create_model, so the @function_tool exposes `confirmed` (no
    # underscore). The bridge wire-protocol has always used "confirmed".
    timeout_ms = args.pop("timeout_ms", None) or DEFAULT_TIMEOUT_MS
    confirmed = args.pop("confirmed", False)
    payload = {
        "action": action,
        "args": args,
        "timeout_ms": timeout_ms,
        "confirmed": confirmed,
    }
    # Add 5s slack for HTTP overhead so the bridge's own timeout fires
    # first and we get its structured 504 instead of an aiohttp raise.
    http_timeout = aiohttp.ClientTimeout(total=(timeout_ms / 1000.0) + 5.0)
    headers = {"Authorization": f"Bearer {LOCAL_TOKEN}"} if LOCAL_TOKEN else {}

    async def _one_shot(session: aiohttp.ClientSession) -> dict:
        try:
            async with session.post(
                f"{BRIDGE_URL}/api/ext_browse",
                json=payload,
                headers=headers,
            ) as r:
                try:
                    data = await r.json()
                except Exception:
                    text = await r.text()
                    data = {
                        "ok": False,
                        "error": f"non-json response (status={r.status}): {text[:200]}",
                    }
                if not data.get("ok") and r.status >= 500:
                    logger.warning(f"[browser-ext] {action} → status={r.status} {data}")
                return data
        except Exception as e:
            return {"ok": False, "error": f"bridge unreachable: {e}"}

    async with aiohttp.ClientSession(timeout=http_timeout) as s:
        data = await _one_shot(s)
        # Auto-launch path: if Chrome isn't connected, the bridge
        # returns 503 with error "extension not connected". Launch
        # Chrome, wait for the extension to register, retry ONCE.
        if (
            not _AUTOLAUNCH_DISABLE
            and not data.get("ok")
            and "extension not connected" in str(data.get("error", "")).lower()
        ):
            if await _launch_chrome_and_wait(s):
                logger.info(f"[browser-ext] retrying {action} after auto-launch")
                data = await _one_shot(s)
        return data


def summarize(result: dict, max_chars: int = 800) -> str:
    """Convert the bridge's structured response to a string the LLM
    can voice. The browser subagent's prompt expects one short
    sentence, so we trim verbose payloads (DOM summaries, page text)
    here rather than relying on the LLM's discipline."""
    if not result.get("ok"):
        return f"Browser command failed: {result.get('error', 'unknown error')}"
    # Most results have a `value` or `summary` field; fall back to repr.
    for key in ("summary", "value", "text", "url", "result"):
        if key in result and result[key] is not None:
            v = str(result[key])
            return v if len(v) <= max_chars else v[:max_chars] + "…"
    # Drop the `ok` flag and dump what's left
    rest = {k: v for k, v in result.items() if k != "ok"}
    return str(rest)[:max_chars] if rest else "Done."
