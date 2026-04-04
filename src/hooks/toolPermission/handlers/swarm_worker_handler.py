"""Swarm worker permission handler.

When running as a swarm worker:
1. Tries classifier auto-approval for bash commands
2. Forwards permission request to leader via mailbox
3. Registers callbacks for when leader responds
4. Sets pending indicator while waiting
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Dict, Optional

from ..permission_context import (
    PermissionContext,
    PermissionDecisionResult,
    create_resolve_once,
)


async def handle_swarm_worker_permission(
    ctx: PermissionContext,
    description: str,
    is_swarm_worker: Callable[[], bool],
    send_permission_request: Optional[Callable] = None,
    register_callback: Optional[Callable] = None,
    pending_classifier_check: Optional[dict] = None,
    updated_input: Optional[dict] = None,
    suggestions: Optional[list] = None,
) -> Optional[PermissionDecisionResult]:
    """Handle the swarm worker permission flow.

    Returns a PermissionDecisionResult if classifier auto-approves,
    or a result from the leader's response.
    Returns None if swarms are not enabled or this is not a swarm worker.

    Equivalent to handleSwarmWorkerPermission TypeScript function.
    """
    if not is_swarm_worker():
        return None

    # Try classifier auto-approval for bash
    if ctx.tool_name == "bash" and pending_classifier_check:
        classifier_result = await ctx.run_classifier()
        if classifier_result and classifier_result.behavior == "allow":
            return classifier_result

    # Forward to leader via mailbox
    if send_permission_request:
        future: asyncio.Future = asyncio.get_event_loop().create_future()

        def on_response(decision: str, updated: Optional[dict] = None, updates: Optional[list] = None, feedback: Optional[str] = None):
            if not future.done():
                future.set_result(PermissionDecisionResult(
                    behavior="allow" if decision == "approved" else "deny",
                    updated_input=updated,
                    permission_updates=updates,
                    feedback=feedback,
                ))

        request_id = await send_permission_request(ctx, description)
        if register_callback and request_id:
            register_callback(request_id, on_response)
            return await future

    return None
