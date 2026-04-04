"""Swarm permission polling for worker agents."""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Dict, List, Optional, Set

POLL_INTERVAL_MS = 500

# Module-level callback registries
_permission_callbacks: Dict[str, dict] = {}
_sandbox_permission_callbacks: Dict[str, dict] = {}


def has_permission_callback(request_id: str) -> bool:
    return request_id in _permission_callbacks


def has_sandbox_permission_callback(request_id: str) -> bool:
    return request_id in _sandbox_permission_callbacks


def register_permission_callback(
    request_id: str, on_allow: Callable, on_reject: Callable
) -> None:
    _permission_callbacks[request_id] = {
        "on_allow": on_allow,
        "on_reject": on_reject,
    }


def remove_permission_callback(request_id: str) -> None:
    _permission_callbacks.pop(request_id, None)


def process_mailbox_permission_response(
    request_id: str,
    decision: str,
    updated_input: Optional[dict] = None,
    permission_updates: Optional[list] = None,
    feedback: Optional[str] = None,
) -> None:
    """Process a permission response from the mailbox."""
    cb = _permission_callbacks.pop(request_id, None)
    if not cb:
        return

    if decision == "approved":
        cb["on_allow"](updated_input, permission_updates)
    else:
        cb["on_reject"](feedback)


def process_sandbox_permission_response(
    request_id: str,
    host: str,
    allow: bool,
) -> None:
    """Process a sandbox permission response."""
    cb = _sandbox_permission_callbacks.pop(request_id, None)
    if not cb:
        return

    cb["on_response"](host, allow)


class SwarmPermissionPoller:
    """Polls for permission responses from the team leader.

    Used when running as a worker agent in a swarm.

    Equivalent to useSwarmPermissionPoller React hook.
    """

    def __init__(
        self,
        is_worker: Callable[[], bool],
        poll_for_response: Callable,
        remove_response: Callable,
        get_agent_name: Callable[[], str],
        get_team_name: Callable[[], Optional[str]],
    ):
        self._is_worker = is_worker
        self._poll_for_response = poll_for_response
        self._remove_response = remove_response
        self._get_agent_name = get_agent_name
        self._get_team_name = get_team_name
        self._running = False

    async def start(self) -> None:
        """Start polling for permission responses."""
        self._running = True
        while self._running:
            if self._is_worker():
                await self._poll()
            await asyncio.sleep(POLL_INTERVAL_MS / 1000)

    def stop(self) -> None:
        self._running = False

    async def _poll(self) -> None:
        """Check for pending permission responses."""
        for request_id in list(_permission_callbacks.keys()):
            response = await self._poll_for_response(
                self._get_agent_name(),
                request_id,
                self._get_team_name(),
            )
            if response:
                self._remove_response(
                    self._get_agent_name(), request_id, self._get_team_name()
                )
                process_mailbox_permission_response(
                    request_id=request_id,
                    decision=response.get("decision", "rejected"),
                    updated_input=response.get("updated_input"),
                    permission_updates=response.get("permission_updates"),
                    feedback=response.get("feedback"),
                )
