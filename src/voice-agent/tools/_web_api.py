"""Shared HTTP layer for the ``web_*`` voice tools (voice agent → JARVIS web app).

The voice agent and the web app run on the same box, so these tools call the
web app's REST API over loopback with the shared ``JARVIS_LOCAL_API_TOKEN``
bearer — the same token the desktop bridge and the web app already share,
written to ``~/.jarvis/local-api-token.env`` and loaded into the voice-agent's
environment by its systemd unit. That bearer clears the web app's ``proxy.ts``
gate for every ``/api/*`` route; routes whose handler ALSO re-checks a user
cookie need a separate SELF_AUTH carve-out on the web side (handled per-tool).

Base URL defaults to the LOCAL web (``127.0.0.1:3000``): the workbench, the
computer-use sidecar, and the X11 desktop all act on THIS machine, so the VPS
would be the wrong target for those. Override with ``JARVIS_WEB_URL`` (e.g. to
point cloud-code tools at the VPS).

Every ``web_*`` tool routes its HTTP through :func:`request_web`, which is the
single indirection point the unit tests patch to avoid real network I/O.
"""
from __future__ import annotations

import os
from typing import Any, Mapping, Optional

import httpx

_DEFAULT_WEB_URL = "http://127.0.0.1:3000"
_TIMEOUT_S = 15.0


def web_url() -> str:
    """The web app base URL (no trailing slash). Local by default."""
    return os.environ.get("JARVIS_WEB_URL", _DEFAULT_WEB_URL).rstrip("/")


def auth_headers() -> dict[str, str]:
    """Bearer header for the target web, read at CALL time.

    ``JARVIS_WEB_TOKEN`` wins when set — use it to point at a REMOTE web whose
    shared token differs from this box's (e.g. the VPS at 0wlan.com, whose
    ``JARVIS_LOCAL_API_TOKEN`` is its own value). Otherwise fall back to this
    box's ``JARVIS_LOCAL_API_TOKEN`` (the right key for a LOCAL web on
    127.0.0.1). Read-at-call (not import) so a token provisioned after the
    module loaded still takes effect — matches kiosk_tool / the bridge tools.
    """
    tok = (
        os.environ.get("JARVIS_WEB_TOKEN", "").strip()
        or os.environ.get("JARVIS_LOCAL_API_TOKEN", "").strip()
    )
    return {"Authorization": f"Bearer {tok}"} if tok else {}


async def request_web(
    method: str,
    path: str,
    *,
    json_body: Optional[Any] = None,
    params: Optional[Mapping[str, Any]] = None,
    timeout: float = _TIMEOUT_S,
) -> httpx.Response:
    """Issue one authenticated request to the web app.

    THE single seam the ``web_*`` tool tests patch. ``path`` is joined to
    :func:`web_url` (e.g. ``"/api/workspace"``).
    """
    async with httpx.AsyncClient(timeout=timeout) as client:
        return await client.request(
            method.upper(),
            f"{web_url()}{path}",
            json=json_body,
            params=dict(params) if params else None,
            headers=auth_headers(),
        )


class WebError(Exception):
    """A voice-speakable failure from a ``web_*`` call.

    Tools catch this and pass ``str(e)`` to ``tool_error`` — the message is
    written to be read aloud, not to be parsed.
    """


def _safe_text(resp: Any) -> str:
    try:
        return (getattr(resp, "text", "") or "")[:200]
    except Exception:  # noqa: BLE001 — best-effort error detail
        return ""


async def call_web(
    method: str,
    path: str,
    *,
    json_body: Optional[Any] = None,
    params: Optional[Mapping[str, Any]] = None,
) -> Any:
    """Call the web app and return parsed JSON, or raise :class:`WebError`.

    Centralizes the failure mapping (unreachable / auth / non-2xx / non-JSON)
    so every ``web_*`` tool speaks the same friendly errors. Tests patch
    :func:`request_web`, so this real mapping is exercised end to end.
    """
    try:
        resp = await request_web(method, path, json_body=json_body, params=params)
    except httpx.ConnectError as e:  # web app not running / wrong port
        raise WebError(
            f"couldn't reach the JARVIS web app at {web_url()} — is it running?"
        ) from e
    except httpx.HTTPError as e:  # timeout, DNS, protocol, …
        raise WebError(f"web request failed: {e}") from e

    sc = int(getattr(resp, "status_code", 0) or 0)
    if sc in (401, 403):
        raise WebError(
            "the web app rejected the request (auth) — check JARVIS_LOCAL_API_TOKEN"
        )
    if sc >= 400:
        detail = _safe_text(resp)
        raise WebError(f"web app returned HTTP {sc}{': ' + detail if detail else ''}")

    # 204 No Content (e.g. DELETE) or an empty body → success with no payload.
    if sc == 204 or not _safe_text(resp).strip():
        return None
    try:
        return resp.json()
    except Exception as e:  # noqa: BLE001
        raise WebError("web app returned a non-JSON response") from e
