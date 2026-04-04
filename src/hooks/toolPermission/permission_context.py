"""Permission context for tool permission decisions.

Manages the full permission lifecycle: check rules, run hooks,
classifier auto-approval, interactive prompts, and telemetry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


@dataclass
class PermissionApprovalSource:
    """Source of a permission approval."""
    source: str  # 'user', 'always_allow', 'hook', 'classifier', 'config'


@dataclass
class PermissionRejectionSource:
    """Source of a permission rejection."""
    source: str  # 'user', 'hook', 'deny_rule', 'config'


@dataclass
class PermissionDecisionResult:
    behavior: str  # 'allow', 'deny', 'ask'
    message: Optional[str] = None
    updated_input: Optional[dict] = None
    permission_updates: Optional[list] = None
    feedback: Optional[str] = None


class PermissionContext:
    """Full permission context for a tool use request.

    Manages: tool info, input, permission rules, hooks, classifier,
    and interactive prompt callbacks.

    Equivalent to PermissionContext TypeScript class.
    """

    def __init__(
        self,
        tool_name: str,
        tool_input: Dict[str, Any],
        tool_use_id: str,
        message_id: str,
        permission_context: Optional[dict] = None,
        run_hooks: Optional[Callable] = None,
        execute_classifier: Optional[Callable] = None,
    ):
        self.tool_name = tool_name
        self.tool_input = tool_input
        self.tool_use_id = tool_use_id
        self.message_id = message_id
        self._permission_context = permission_context or {}
        self._run_hooks = run_hooks
        self._execute_classifier = execute_classifier
        self._resolved = False
        self._result: Optional[PermissionDecisionResult] = None

    async def run_hooks_check(
        self,
        permission_mode: Optional[str] = None,
        suggestions: Optional[list] = None,
        updated_input: Optional[dict] = None,
    ) -> Optional[PermissionDecisionResult]:
        """Run permission hooks. Returns result if hooks resolved, None to continue."""
        if self._run_hooks:
            return await self._run_hooks(permission_mode, suggestions, updated_input)
        return None

    async def run_classifier(self) -> Optional[PermissionDecisionResult]:
        """Run classifier auto-approval. Returns result if approved, None to continue."""
        if self._execute_classifier:
            return await self._execute_classifier(self.tool_name, self.tool_input)
        return None

    def resolve(self, result: PermissionDecisionResult) -> None:
        """Resolve the permission request with a final decision."""
        if not self._resolved:
            self._resolved = True
            self._result = result

    @property
    def is_resolved(self) -> bool:
        return self._resolved

    @property
    def result(self) -> Optional[PermissionDecisionResult]:
        return self._result


def create_resolve_once(resolve: Callable) -> Callable:
    """Create a callback that can only be called once."""
    called = False

    def resolve_once(*args, **kwargs):
        nonlocal called
        if called:
            return
        called = True
        resolve(*args, **kwargs)

    return resolve_once
