"""Create a session on a direct-connect server — Python equivalent of createDirectConnectSession.ts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

import aiohttp

from .directConnectManager import DirectConnectConfig


class DirectConnectError(Exception):
    """Errors thrown when the direct-connect session creation fails."""
    pass


@dataclass
class DirectConnectSessionResult:
    config: DirectConnectConfig
    work_dir: Optional[str] = None


async def create_direct_connect_session(
    *,
    server_url: str,
    auth_token: Optional[str] = None,
    cwd: str,
    dangerously_skip_permissions: bool = False,
) -> DirectConnectSessionResult:
    """Create a session on a direct-connect server.

    Posts to ``{server_url}/sessions``, validates the response, and returns
    a DirectConnectConfig ready for use by the REPL or headless runner.

    Raises DirectConnectError on network, HTTP, or response-parsing failures.
    """
    headers: dict[str, str] = {"content-type": "application/json"}
    if auth_token:
        headers["authorization"] = f"Bearer {auth_token}"

    body: dict = {"cwd": cwd}
    if dangerously_skip_permissions:
        body["dangerously_skip_permissions"] = True

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{server_url}/sessions",
                headers=headers,
                json=body,
            ) as resp:
                if not resp.ok:
                    raise DirectConnectError(
                        f"Failed to create session: {resp.status} {resp.reason}"
                    )

                data = await resp.json()
    except aiohttp.ClientError as exc:
        raise DirectConnectError(
            f"Failed to connect to server at {server_url}: {exc}"
        ) from exc

    # Validate required fields
    if not isinstance(data, dict) or "session_id" not in data or "ws_url" not in data:
        raise DirectConnectError(f"Invalid session response: missing required fields")

    return DirectConnectSessionResult(
        config=DirectConnectConfig(
            server_url=server_url,
            session_id=data["session_id"],
            ws_url=data["ws_url"],
            auth_token=auth_token,
        ),
        work_dir=data.get("work_dir"),
    )
