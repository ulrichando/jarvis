"""Bridge API client for Remote Control environment management."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Callable, Optional

import aiohttp

from .types import (
    BRIDGE_LOGIN_INSTRUCTION,
    BridgeConfig,
    PermissionResponseEvent,
    WorkResponse,
)

logger = logging.getLogger(__name__)

BETA_HEADER = "environments-2025-11-01"
SAFE_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")


def validate_bridge_id(id_val: str, label: str) -> str:
    """Validate that a server-provided ID is safe to interpolate into a URL path."""
    if not id_val or not SAFE_ID_PATTERN.match(id_val):
        raise ValueError(f"Invalid {label}: contains unsafe characters")
    return id_val


class BridgeFatalError(Exception):
    """Fatal bridge errors that should not be retried."""
    def __init__(self, message: str, status: int, error_type: Optional[str] = None):
        super().__init__(message)
        self.status = status
        self.error_type = error_type


def extract_error_detail(data: Any) -> Optional[str]:
    """Extract error detail from response data."""
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict):
            return error.get("message") or error.get("detail")
        if isinstance(error, str):
            return error
        return data.get("message") or data.get("detail")
    return None


def extract_error_type_from_data(data: Any) -> Optional[str]:
    """Extract error type from response data."""
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict):
            t = error.get("type")
            if isinstance(t, str):
                return t
    return None


def is_expired_error_type(error_type: Optional[str]) -> bool:
    """Check whether an error type string indicates expiry."""
    if not error_type:
        return False
    return "expired" in error_type or "lifetime" in error_type


def is_suppressible_403(err: BridgeFatalError) -> bool:
    """Check whether a BridgeFatalError is a suppressible 403 permission error."""
    if err.status != 403:
        return False
    return "external_poll_sessions" in str(err) or "environments:manage" in str(err)


def _handle_error_status(status: int, data: Any, context: str) -> None:
    """Handle non-success HTTP status codes."""
    if status in (200, 204):
        return
    detail = extract_error_detail(data)
    error_type = extract_error_type_from_data(data)

    if status == 401:
        msg = f"{context}: Authentication failed (401)"
        if detail:
            msg += f": {detail}"
        msg += f". {BRIDGE_LOGIN_INSTRUCTION}"
        raise BridgeFatalError(msg, 401, error_type)
    elif status == 403:
        if is_expired_error_type(error_type):
            raise BridgeFatalError(
                "Remote Control session has expired. Please restart with `claude remote-control` or /remote-control.",
                403, error_type,
            )
        msg = f"{context}: Access denied (403)"
        if detail:
            msg += f": {detail}"
        msg += ". Check your organization permissions."
        raise BridgeFatalError(msg, 403, error_type)
    elif status == 404:
        raise BridgeFatalError(
            detail or f"{context}: Not found (404). Remote Control may not be available for this organization.",
            404, error_type,
        )
    elif status == 410:
        raise BridgeFatalError(
            detail or "Remote Control session has expired. Please restart with `claude remote-control` or /remote-control.",
            410, error_type or "environment_expired",
        )
    elif status == 429:
        raise RuntimeError(f"{context}: Rate limited (429). Polling too frequently.")
    else:
        msg = f"{context}: Failed with status {status}"
        if detail:
            msg += f": {detail}"
        raise RuntimeError(msg)


@dataclass
class BridgeApiDeps:
    base_url: str
    get_access_token: Callable[[], Optional[str]]
    runner_version: str
    on_debug: Optional[Callable[[str], None]] = None
    on_auth_401: Optional[Callable[[str], Any]] = None
    get_trusted_device_token: Optional[Callable[[], Optional[str]]] = None


def create_bridge_api_client(deps: BridgeApiDeps):
    """Create a bridge API client with the given dependencies."""
    consecutive_empty_polls = 0
    EMPTY_POLL_LOG_INTERVAL = 100

    def debug(msg: str) -> None:
        if deps.on_debug:
            deps.on_debug(msg)

    def get_headers(access_token: str) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
            "anthropic-beta": BETA_HEADER,
            "x-environment-runner-version": deps.runner_version,
        }
        if deps.get_trusted_device_token:
            device_token = deps.get_trusted_device_token()
            if device_token:
                headers["X-Trusted-Device-Token"] = device_token
        return headers

    def resolve_auth() -> str:
        access_token = deps.get_access_token()
        if not access_token:
            raise RuntimeError(BRIDGE_LOGIN_INSTRUCTION)
        return access_token

    class Client:
        async def register_bridge_environment(self, config: BridgeConfig) -> dict[str, str]:
            debug(f"[bridge:api] POST /v1/environments/bridge bridgeId={config.bridge_id}")
            access_token = resolve_auth()
            body = {
                "machine_name": config.machine_name,
                "directory": config.dir,
                "branch": config.branch,
                "git_repo_url": config.git_repo_url,
                "max_sessions": config.max_sessions,
                "metadata": {"worker_type": config.worker_type},
            }
            if config.reuse_environment_id:
                body["environment_id"] = config.reuse_environment_id

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{deps.base_url}/v1/environments/bridge",
                    json=body,
                    headers=get_headers(access_token),
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    data = await resp.json()
                    _handle_error_status(resp.status, data, "Registration")
                    return data

        async def poll_for_work(
            self, environment_id: str, environment_secret: str,
            signal: Optional[Any] = None, reclaim_older_than_ms: Optional[int] = None,
        ) -> Optional[dict]:
            nonlocal consecutive_empty_polls
            validate_bridge_id(environment_id, "environmentId")
            prev_empty = consecutive_empty_polls
            consecutive_empty_polls = 0

            params = {}
            if reclaim_older_than_ms is not None:
                params["reclaim_older_than_ms"] = reclaim_older_than_ms

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{deps.base_url}/v1/environments/{environment_id}/work/poll",
                    headers=get_headers(environment_secret),
                    params=params or None,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    data = await resp.json() if resp.content_length else None
                    _handle_error_status(resp.status, data, "Poll")
                    if not data:
                        consecutive_empty_polls = prev_empty + 1
                        if consecutive_empty_polls == 1 or consecutive_empty_polls % EMPTY_POLL_LOG_INTERVAL == 0:
                            debug(f"[bridge:api] GET .../work/poll -> {resp.status} (no work, {consecutive_empty_polls} consecutive empty polls)")
                        return None
                    debug(f"[bridge:api] GET .../work/poll -> {resp.status} workId={data.get('id')}")
                    return data

        async def acknowledge_work(self, environment_id: str, work_id: str, session_token: str) -> None:
            validate_bridge_id(environment_id, "environmentId")
            validate_bridge_id(work_id, "workId")
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{deps.base_url}/v1/environments/{environment_id}/work/{work_id}/ack",
                    json={},
                    headers=get_headers(session_token),
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    data = await resp.json() if resp.content_length else None
                    _handle_error_status(resp.status, data, "Acknowledge")

        async def stop_work(self, environment_id: str, work_id: str, force: bool) -> None:
            validate_bridge_id(environment_id, "environmentId")
            validate_bridge_id(work_id, "workId")
            access_token = resolve_auth()
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{deps.base_url}/v1/environments/{environment_id}/work/{work_id}/stop",
                    json={"force": force},
                    headers=get_headers(access_token),
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    data = await resp.json() if resp.content_length else None
                    _handle_error_status(resp.status, data, "StopWork")

        async def deregister_environment(self, environment_id: str) -> None:
            validate_bridge_id(environment_id, "environmentId")
            access_token = resolve_auth()
            async with aiohttp.ClientSession() as session:
                async with session.delete(
                    f"{deps.base_url}/v1/environments/bridge/{environment_id}",
                    headers=get_headers(access_token),
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    data = await resp.json() if resp.content_length else None
                    _handle_error_status(resp.status, data, "Deregister")

        async def archive_session(self, session_id: str) -> None:
            validate_bridge_id(session_id, "sessionId")
            access_token = resolve_auth()
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{deps.base_url}/v1/sessions/{session_id}/archive",
                    json={},
                    headers=get_headers(access_token),
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 409:
                        return
                    data = await resp.json() if resp.content_length else None
                    _handle_error_status(resp.status, data, "ArchiveSession")

        async def reconnect_session(self, environment_id: str, session_id: str) -> None:
            validate_bridge_id(environment_id, "environmentId")
            validate_bridge_id(session_id, "sessionId")
            access_token = resolve_auth()
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{deps.base_url}/v1/environments/{environment_id}/bridge/reconnect",
                    json={"session_id": session_id},
                    headers=get_headers(access_token),
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    data = await resp.json() if resp.content_length else None
                    _handle_error_status(resp.status, data, "ReconnectSession")

        async def heartbeat_work(
            self, environment_id: str, work_id: str, session_token: str,
        ) -> dict[str, Any]:
            validate_bridge_id(environment_id, "environmentId")
            validate_bridge_id(work_id, "workId")
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{deps.base_url}/v1/environments/{environment_id}/work/{work_id}/heartbeat",
                    json={},
                    headers=get_headers(session_token),
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    data = await resp.json()
                    _handle_error_status(resp.status, data, "Heartbeat")
                    return data

        async def send_permission_response_event(
            self, session_id: str, event: dict, session_token: str,
        ) -> None:
            validate_bridge_id(session_id, "sessionId")
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{deps.base_url}/v1/sessions/{session_id}/events",
                    json={"events": [event]},
                    headers=get_headers(session_token),
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    data = await resp.json() if resp.content_length else None
                    _handle_error_status(resp.status, data, "SendPermissionResponseEvent")

    return Client()
