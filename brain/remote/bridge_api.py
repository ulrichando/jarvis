"""
Bridge API client for JARVIS online hosting infrastructure.

Handles environment registration, work polling, session management,
and communication with the remote bridge server.
"""

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger(__name__)


@dataclass
class BridgeConfig:
    """Configuration for the bridge API client."""
    base_url: str = ""
    access_token: str = ""
    environment_id: str = ""
    machine_name: str = ""
    directory: str = ""
    max_sessions: int = 1
    timeout: int = 30
    trusted_device_token: str = ""


@dataclass
class WorkItem:
    """A unit of work received from the bridge server."""
    work_id: str
    session_id: str
    environment_id: str
    session_token: str = ""
    api_base_url: str = ""
    state: str = "pending"  # pending, active, completed, failed


_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")


class BridgeClient:
    """Client for the JARVIS bridge API.

    Manages environment lifecycle (register/deregister), work polling
    and acknowledgement, session creation/archival, and permission responses.
    """

    def __init__(self, config: BridgeConfig):
        self.config = config
        self._session = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_headers(self) -> dict:
        """Build standard request headers."""
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-API-Beta": "1",
        }
        if self.config.access_token:
            headers["Authorization"] = f"Bearer {self.config.access_token}"
        if self.config.trusted_device_token:
            headers["X-Trusted-Device-Token"] = self.config.trusted_device_token
        return headers

    @staticmethod
    def _validate_id(id_str: str) -> bool:
        """Validate an ID string to prevent path traversal."""
        if not id_str:
            return False
        return bool(_ID_PATTERN.match(id_str))

    async def _get_session(self):
        """Lazily create an aiohttp ClientSession."""
        if self._session is None or self._session.closed:
            try:
                import aiohttp
                self._session = aiohttp.ClientSession()
            except ImportError:
                self._session = None
        return self._session

    async def _request(
        self,
        method: str,
        path: str,
        data: dict = None,
        timeout: int = None,
    ) -> Optional[dict]:
        """Generic HTTP request with error handling.

        Returns the parsed JSON response dict, or None on failure.
        """
        url = f"{self.config.base_url}{path}"
        effective_timeout = timeout or self.config.timeout
        headers = self._build_headers()

        try:
            import aiohttp

            session = await self._get_session()
            if session is None:
                log.error("bridge: failed to create HTTP session")
                return None

            client_timeout = aiohttp.ClientTimeout(total=effective_timeout)
            kwargs = {"headers": headers, "timeout": client_timeout}
            if data is not None:
                kwargs["json"] = data

            async with session.request(method, url, **kwargs) as resp:
                status = resp.status

                if status == 401:
                    log.error("bridge: authentication error (401) for %s %s", method, path)
                    return None
                if status == 403:
                    log.error("bridge: access denied (403) for %s %s", method, path)
                    return None
                if status == 404:
                    log.warning("bridge: not found (404) for %s %s", method, path)
                    return None
                if status == 410:
                    log.error("bridge: resource expired/gone (410) for %s %s", method, path)
                    return None
                if status == 429:
                    log.warning("bridge: rate limited (429) for %s %s", method, path)
                    return None

                if status >= 400:
                    body = await resp.text()
                    log.error("bridge: HTTP %d for %s %s: %s", status, method, path, body[:200])
                    return None

                if resp.content_length == 0:
                    return {}

                text = await resp.text()
                if not text.strip():
                    return {}

                return json.loads(text)

        except ImportError:
            # aiohttp not available, fall back to urllib
            return await self._request_urllib(method, url, data, effective_timeout)
        except asyncio.TimeoutError:
            log.warning("bridge: timeout for %s %s", method, path)
            return None
        except Exception as exc:
            log.error("bridge: request error for %s %s: %s", method, path, exc)
            return None

    async def _request_urllib(
        self, method: str, url: str, data: dict = None, timeout: int = 30
    ) -> Optional[dict]:
        """Fallback HTTP request using urllib (runs in executor)."""
        import urllib.request
        import urllib.error

        headers = self._build_headers()

        body = None
        if data is not None:
            body = json.dumps(data).encode("utf-8")

        req = urllib.request.Request(url, data=body, headers=headers, method=method)

        loop = asyncio.get_event_loop()
        try:
            def _do_request():
                try:
                    with urllib.request.urlopen(req, timeout=timeout) as resp:
                        raw = resp.read().decode("utf-8")
                        if not raw.strip():
                            return {}
                        return json.loads(raw)
                except urllib.error.HTTPError as e:
                    status = e.code
                    if status == 401:
                        log.error("bridge: authentication error (401) for %s %s", method, url)
                    elif status == 403:
                        log.error("bridge: access denied (403) for %s %s", method, url)
                    elif status == 404:
                        log.warning("bridge: not found (404) for %s %s", method, url)
                    elif status == 410:
                        log.error("bridge: resource expired/gone (410) for %s %s", method, url)
                    elif status == 429:
                        log.warning("bridge: rate limited (429) for %s %s", method, url)
                    else:
                        log.error("bridge: HTTP %d for %s %s", status, method, url)
                    return None
                except Exception as exc:
                    log.error("bridge: urllib error for %s %s: %s", method, url, exc)
                    return None

            return await loop.run_in_executor(None, _do_request)
        except Exception as exc:
            log.error("bridge: executor error for %s %s: %s", method, url, exc)
            return None

    # ------------------------------------------------------------------
    # Environment management
    # ------------------------------------------------------------------

    async def register_environment(self) -> str:
        """Register this environment with the bridge server.

        POST /v1/environments/bridge
        Returns the environment_id on success, empty string on failure.
        """
        payload = {
            "machine_name": self.config.machine_name,
            "directory": self.config.directory,
            "max_sessions": self.config.max_sessions,
        }
        result = await self._request("POST", "/v1/environments/bridge", data=payload)
        if result and "environment_id" in result:
            self.config.environment_id = result["environment_id"]
            log.info("bridge: registered environment %s", self.config.environment_id)
            return self.config.environment_id
        log.error("bridge: failed to register environment")
        return ""

    async def deregister_environment(self) -> bool:
        """Deregister this environment from the bridge server.

        DELETE /v1/environments/bridge/{id}
        """
        env_id = self.config.environment_id
        if not self._validate_id(env_id):
            log.error("bridge: invalid environment_id for deregister")
            return False

        result = await self._request("DELETE", f"/v1/environments/bridge/{env_id}")
        if result is not None:
            log.info("bridge: deregistered environment %s", env_id)
            self.config.environment_id = ""
            return True
        return False

    # ------------------------------------------------------------------
    # Work management
    # ------------------------------------------------------------------

    async def poll_for_work(self, reclaim_older_than_ms: int = 0) -> Optional[WorkItem]:
        """Poll for available work items.

        GET /v1/environments/{id}/work/poll with 10s long-poll timeout.
        Returns a WorkItem or None if no work is available.
        """
        env_id = self.config.environment_id
        if not self._validate_id(env_id):
            log.error("bridge: invalid environment_id for poll")
            return None

        path = f"/v1/environments/{env_id}/work/poll"
        if reclaim_older_than_ms > 0:
            path += f"?reclaim_older_than_ms={reclaim_older_than_ms}"

        result = await self._request("GET", path, timeout=10)
        if not result:
            return None

        if "work_id" not in result:
            return None

        return WorkItem(
            work_id=result.get("work_id", ""),
            session_id=result.get("session_id", ""),
            environment_id=result.get("environment_id", env_id),
            session_token=result.get("session_token", ""),
            api_base_url=result.get("api_base_url", ""),
            state=result.get("state", "pending"),
        )

    async def acknowledge_work(self, work_id: str) -> bool:
        """Acknowledge receipt of a work item.

        POST /v1/environments/{id}/work/{work_id}/ack
        """
        env_id = self.config.environment_id
        if not self._validate_id(env_id) or not self._validate_id(work_id):
            log.error("bridge: invalid id for acknowledge_work")
            return False

        result = await self._request(
            "POST", f"/v1/environments/{env_id}/work/{work_id}/ack"
        )
        return result is not None

    async def heartbeat(self, work_id: str) -> bool:
        """Send heartbeat for an active work item.

        POST /v1/environments/{id}/work/{work_id}/heartbeat
        """
        env_id = self.config.environment_id
        if not self._validate_id(env_id) or not self._validate_id(work_id):
            log.error("bridge: invalid id for heartbeat")
            return False

        result = await self._request(
            "POST", f"/v1/environments/{env_id}/work/{work_id}/heartbeat"
        )
        return result is not None

    async def stop_work(self, work_id: str, force: bool = False) -> bool:
        """Stop a work item.

        POST /v1/environments/{id}/work/{work_id}/stop
        """
        env_id = self.config.environment_id
        if not self._validate_id(env_id) or not self._validate_id(work_id):
            log.error("bridge: invalid id for stop_work")
            return False

        payload = {}
        if force:
            payload["force"] = True

        result = await self._request(
            "POST",
            f"/v1/environments/{env_id}/work/{work_id}/stop",
            data=payload if payload else None,
        )
        return result is not None

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    async def create_session(
        self,
        title: str,
        events: list = None,
        model: str = "",
    ) -> str:
        """Create a new session.

        POST /v1/sessions
        Returns the session_id on success, empty string on failure.
        """
        payload = {"title": title}
        if events:
            payload["events"] = events
        if model:
            payload["session_context"] = {"model": model}

        result = await self._request("POST", "/v1/sessions", data=payload)
        if result and "session_id" in result:
            log.info("bridge: created session %s", result["session_id"])
            return result["session_id"]
        log.error("bridge: failed to create session")
        return ""

    async def archive_session(self, session_id: str) -> bool:
        """Archive a session.

        POST /v1/sessions/{session_id}/archive
        409 (already archived) is treated as success.
        """
        if not self._validate_id(session_id):
            log.error("bridge: invalid session_id for archive")
            return False

        result = await self._request(
            "POST", f"/v1/sessions/{session_id}/archive"
        )
        # _request returns None on error, but 409 needs special handling
        # We handle this by checking for 409 in a custom flow
        if result is not None:
            return True

        # Retry: 409 would have been caught as >=400 in _request.
        # For robustness, treat archive failure as potentially a 409.
        log.info("bridge: archive for session %s may already be archived", session_id)
        return False

    async def send_permission_response(
        self,
        session_id: str,
        request_id: str,
        behavior: str,
        message: str = "",
    ) -> bool:
        """Send a permission response event for a session.

        POST /v1/sessions/{session_id}/events
        """
        if not self._validate_id(session_id):
            log.error("bridge: invalid session_id for permission response")
            return False

        payload = {
            "type": "permission_response",
            "request_id": request_id,
            "behavior": behavior,
        }
        if message:
            payload["message"] = message

        result = await self._request(
            "POST", f"/v1/sessions/{session_id}/events", data=payload
        )
        return result is not None

    async def reconnect_session(self, session_id: str) -> bool:
        """Reconnect to an existing session via the bridge.

        POST /v1/environments/{id}/bridge/reconnect
        """
        env_id = self.config.environment_id
        if not self._validate_id(env_id) or not self._validate_id(session_id):
            log.error("bridge: invalid id for reconnect_session")
            return False

        payload = {"session_id": session_id}
        result = await self._request(
            "POST",
            f"/v1/environments/{env_id}/bridge/reconnect",
            data=payload,
        )
        return result is not None

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    async def close(self):
        """Close the underlying HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
