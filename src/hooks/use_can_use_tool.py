"""Tool permission checking and user interaction flow."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional


@dataclass
class PermissionDecision:
    behavior: str  # 'allow', 'ask', 'deny'
    message: Optional[str] = None
    suggestions: Optional[List[str]] = None


class ToolPermissionChecker:
    """Checks tool permissions and manages the ask/allow/deny flow.

    Equivalent to useCanUseTool React hook (very large, >600 lines in TS).
    """

    def __init__(
        self,
        get_permission_context: Callable,
        set_permission_context: Callable,
        check_hooks: Optional[Callable] = None,
    ):
        self._get_context = get_permission_context
        self._set_context = set_permission_context
        self._check_hooks = check_hooks

    def check(
        self,
        tool_name: str,
        tool_input: Dict[str, Any],
    ) -> PermissionDecision:
        """Check if a tool can be used with the given input."""
        context = self._get_context()

        # Check always-allow rules
        always_allow = context.get("always_allow_rules", {})
        for rule in always_allow.get("session", []):
            if self._matches_rule(rule, tool_name, tool_input):
                return PermissionDecision(behavior="allow")

        # Check deny rules
        deny_rules = context.get("deny_rules", [])
        for rule in deny_rules:
            if self._matches_rule(rule, tool_name, tool_input):
                return PermissionDecision(
                    behavior="deny",
                    message=rule.get("message", "Tool denied by rule"),
                )

        # Default: ask
        return PermissionDecision(
            behavior="ask",
            message=f"{tool_name} requires permission",
        )

    def _matches_rule(
        self, rule: dict, tool_name: str, tool_input: Dict[str, Any]
    ) -> bool:
        rule_tool = rule.get("tool_name", "")
        if rule_tool and rule_tool != tool_name:
            return False
        return True

    def add_allow_rule(self, rule: dict, destination: str = "session") -> None:
        context = self._get_context()
        rules = context.setdefault("always_allow_rules", {}).setdefault(destination, [])
        rules.append(rule)
        self._set_context(context)
