"""Coordinator worker permission handler.

Handles permission flow for coordinator workers:
automated checks (hooks + classifier) are awaited before
falling through to interactive dialog.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from ..permission_context import PermissionContext, PermissionDecisionResult


async def handle_coordinator_permission(
    ctx: PermissionContext,
    pending_classifier_check: Optional[dict] = None,
    updated_input: Optional[dict] = None,
    suggestions: Optional[list] = None,
    permission_mode: Optional[str] = None,
) -> Optional[PermissionDecisionResult]:
    """Handle the coordinator worker permission flow.

    Returns a PermissionDecisionResult if automated checks resolved the
    permission, or None if the caller should fall through to the
    interactive dialog.

    Equivalent to handleCoordinatorPermission TypeScript function.
    """
    try:
        # 1. Try permission hooks first (fast, local)
        hook_result = await ctx.run_hooks_check(
            permission_mode, suggestions, updated_input
        )
        if hook_result:
            return hook_result

        # 2. Try classifier (slow, inference -- bash only)
        if ctx.tool_name == "bash" and pending_classifier_check:
            classifier_result = await ctx.run_classifier()
            if classifier_result:
                return classifier_result

    except Exception:
        pass

    return None  # Fall through to interactive
