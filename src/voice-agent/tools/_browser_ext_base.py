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

import logging
import os
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


async def post(action: str, **args: Any) -> dict:
    """Post a command to the bridge. Returns the bridge's JSON response
    verbatim — usually `{ok: bool, ...}`. Network/extension errors
    surface as `{ok: False, error: "..."}` so the LLM gets actionable
    text rather than a Python exception."""
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
    try:
        async with aiohttp.ClientSession(timeout=http_timeout) as s:
            async with s.post(
                f"{BRIDGE_URL}/api/ext_browse",
                json=payload,
                headers=headers,
            ) as r:
                try:
                    data = await r.json()
                except Exception:
                    text = await r.text()
                    data = {"ok": False, "error": f"non-json response (status={r.status}): {text[:200]}"}
                if not data.get("ok") and r.status >= 500:
                    logger.warning(f"[browser-ext] {action} → status={r.status} {data}")
                return data
    except Exception as e:
        return {"ok": False, "error": f"bridge unreachable: {e}"}


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
