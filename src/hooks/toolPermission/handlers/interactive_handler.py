"""Interactive permission handler.

Handles the interactive permission prompt flow:
shows a dialog to the user and waits for their decision.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Dict, Optional

from ..permission_context import (
    PermissionContext,
    PermissionDecisionResult,
    create_resolve_once,
)


async def handle_interactive_permission(
    ctx: PermissionContext,
    description: str,
    show_dialog: Optional[Callable] = None,
    await_automated_first: bool = False,
) -> PermissionDecisionResult:
    """Handle the interactive permission flow.

    Shows a permission dialog to the user and waits for their decision.
    Optionally runs automated checks (hooks, classifier) concurrently.

    Equivalent to handleInteractivePermission TypeScript function.
    """
    future: asyncio.Future = asyncio.get_event_loop().create_future()

    def on_allow(updated_input: Optional[dict] = None, permission_updates: Optional[list] = None):
        if not future.done():
            future.set_result(PermissionDecisionResult(
                behavior="allow",
                updated_input=updated_input,
                permission_updates=permission_updates,
            ))

    def on_reject(feedback: Optional[str] = None):
        if not future.done():
            future.set_result(PermissionDecisionResult(
                behavior="deny",
                feedback=feedback,
            ))

    def on_abort():
        if not future.done():
            future.set_result(PermissionDecisionResult(
                behavior="deny",
                message="User aborted",
            ))

    if show_dialog:
        show_dialog(
            ctx=ctx,
            description=description,
            on_allow=on_allow,
            on_reject=on_reject,
            on_abort=on_abort,
        )

    return await future
