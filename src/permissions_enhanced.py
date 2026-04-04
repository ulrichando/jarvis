"""JARVIS Enhanced Permissions -- policy-based access control with builtin profiles.

Extends brain/permissions.py with regex-based rules, scoped
policies, and predefined policy profiles.

This module provides the PermissionPolicy layer that sits on top of the
existing PermissionManager. It adds:
  - Regex-based tool and path pattern matching
  - Named builtin policies (default, trust, plan, strict)
  - Serialization to/from config dictionaries
  - Scope-aware rules (global, project, session)

Usage:
    policy = PermissionPolicy()
    policy.load_from_config({"rules": [...]})
    result = policy.check("bash", path="/etc/passwd", args={"command": "cat /etc/passwd"})
    # result: "deny"
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("jarvis.permissions_enhanced")


@dataclass
class PermissionRule:
    """A single permission rule that matches tool calls by pattern.

    Attributes:
        tool_pattern: Regex pattern matching tool names (e.g. "bash", "write_.*", ".*").
        path_pattern: Regex pattern matching file paths (e.g. "/etc/.*", ".*\\.py").
                      Empty string matches everything.
        action: What to do when matched -- "allow", "deny", or "ask".
        scope: Where this rule applies -- "global", "project", "session", or "".
    """
    tool_pattern: str = ".*"
    path_pattern: str = ""
    action: str = "allow"
    scope: str = ""

    def __post_init__(self):
        if self.action not in ("allow", "deny", "ask"):
            raise ValueError(f"Invalid action '{self.action}', must be allow/deny/ask")
        self._tool_re = re.compile(self.tool_pattern, re.IGNORECASE)
        self._path_re = re.compile(self.path_pattern, re.IGNORECASE) if self.path_pattern else None

    def matches(self, tool: str, path: str = "") -> bool:
        """Check if this rule matches the given tool call.

        Args:
            tool: Tool name being invoked.
            path: File path involved (if any).

        Returns:
            True if both tool and path patterns match.
        """
        if not self._tool_re.fullmatch(tool):
            return False
        if self._path_re and path:
            if not self._path_re.fullmatch(path):
                return False
        return True

    @property
    def specificity(self) -> int:
        """Higher values mean more specific rules (used for priority)."""
        score = 0
        if self.tool_pattern != ".*":
            score += 2
        if self.path_pattern:
            score += 1
        if self.scope:
            score += 1
        return score

    def to_dict(self) -> dict:
        """Serialize to a dictionary."""
        d = {"tool_pattern": self.tool_pattern, "action": self.action}
        if self.path_pattern:
            d["path_pattern"] = self.path_pattern
        if self.scope:
            d["scope"] = self.scope
        return d

    @classmethod
    def from_dict(cls, d: dict) -> PermissionRule:
        """Deserialize from a dictionary."""
        return cls(
            tool_pattern=d.get("tool_pattern", d.get("tool", ".*")),
            path_pattern=d.get("path_pattern", d.get("path", "")),
            action=d.get("action", d.get("behavior", "allow")),
            scope=d.get("scope", ""),
        )


class PermissionPolicy:
    """Policy-based permission evaluation with ordered rules.

    Rules are evaluated in order of specificity (most specific first).
    The first matching rule determines the outcome. If no rule matches,
    the default action is "ask".
    """

    def __init__(self):
        self._rules: list[PermissionRule] = []

    # -- Rule management ---------------------------------------------------

    def add_rule(self, rule: PermissionRule) -> None:
        """Add a permission rule to the policy."""
        self._rules.append(rule)

    def remove_rule(self, tool_pattern: str, path_pattern: str = "") -> bool:
        """Remove the first rule matching the given patterns. Returns True if found."""
        for i, r in enumerate(self._rules):
            if r.tool_pattern == tool_pattern and r.path_pattern == path_pattern:
                self._rules.pop(i)
                return True
        return False

    def clear(self) -> None:
        """Remove all rules."""
        self._rules.clear()

    @property
    def rules(self) -> list[PermissionRule]:
        """Get a copy of the current rules."""
        return list(self._rules)

    # -- Evaluation --------------------------------------------------------

    def check(self, tool: str, path: str = "", args: dict | None = None) -> str:
        """Evaluate a tool call against the policy.

        Args:
            tool: Tool name being invoked.
            path: File path involved (extracted from args if empty).
            args: Full tool arguments (used to extract path if not provided).

        Returns:
            "allow", "deny", or "ask".
        """
        # Extract path from args if not provided directly
        if not path and args:
            path = (
                args.get("path", "")
                or args.get("file_path", "")
                or args.get("command", "")
            )

        # Gather matching rules sorted by specificity (most specific first)
        matches = [
            (rule.specificity, rule)
            for rule in self._rules
            if rule.matches(tool, path)
        ]
        matches.sort(key=lambda x: x[0], reverse=True)

        if matches:
            return matches[0][1].action

        return "ask"

    # -- Serialization -----------------------------------------------------

    def load_from_config(self, config: dict) -> None:
        """Load rules from a configuration dictionary.

        Expected format:
            {"rules": [{"tool_pattern": "...", "action": "...", ...}, ...]}

        Or a list of rule dicts directly.

        Args:
            config: Configuration dictionary or list.
        """
        rules_data = config if isinstance(config, list) else config.get("rules", [])
        for entry in rules_data:
            try:
                if isinstance(entry, dict):
                    rule = PermissionRule.from_dict(entry)
                    self._rules.append(rule)
                elif isinstance(entry, str):
                    # Support compact format: "action:tool_pattern(path_pattern)"
                    rule = self._parse_compact(entry)
                    self._rules.append(rule)
            except (ValueError, re.error) as exc:
                logger.warning("Skipping invalid permission rule: %s", exc)

    def to_config(self) -> dict:
        """Serialize the policy to a configuration dictionary.

        Returns:
            Dictionary with "rules" key containing list of rule dicts.
        """
        return {
            "rules": [rule.to_dict() for rule in self._rules],
        }

    # -- Helpers -----------------------------------------------------------

    @staticmethod
    def _parse_compact(rule_str: str) -> PermissionRule:
        """Parse a compact rule string into a PermissionRule.

        Format: "action:tool_pattern" or "action:tool_pattern(path_pattern)"
        Examples:
            "allow:read_file"
            "deny:bash(/etc/.*)"
            "ask:write_file(.*\\.py)"
        """
        rule_str = rule_str.strip()
        if ":" not in rule_str:
            raise ValueError(f"Invalid compact rule (missing action prefix): {rule_str!r}")

        action, rest = rule_str.split(":", 1)
        action = action.lower().strip()
        if action not in ("allow", "deny", "ask"):
            raise ValueError(f"Invalid action '{action}', must be allow/deny/ask")

        rest = rest.strip()
        paren_match = re.match(r'^([^(]+)\((.+)\)$', rest)
        if paren_match:
            tool_pattern = paren_match.group(1).strip()
            path_pattern = paren_match.group(2).strip()
        else:
            tool_pattern = rest
            path_pattern = ""

        return PermissionRule(
            tool_pattern=tool_pattern,
            path_pattern=path_pattern,
            action=action,
        )

    def summary(self) -> dict:
        """Return a human-readable summary of the policy."""
        return {
            "rule_count": len(self._rules),
            "rules": [r.to_dict() for r in self._rules],
        }


# ---------------------------------------------------------------------------
# Builtin policies
# ---------------------------------------------------------------------------

def _make_default_policy() -> PermissionPolicy:
    """Default policy: ask for destructive operations, allow read-only."""
    policy = PermissionPolicy()
    # Allow read-only tools
    policy.add_rule(PermissionRule(tool_pattern="read_file", action="allow"))
    policy.add_rule(PermissionRule(tool_pattern="search_files", action="allow"))
    policy.add_rule(PermissionRule(tool_pattern="web_search", action="allow"))
    policy.add_rule(PermissionRule(tool_pattern="web_fetch", action="allow"))
    policy.add_rule(PermissionRule(tool_pattern="think", action="allow"))
    # Ask for write/execute
    policy.add_rule(PermissionRule(tool_pattern="bash", action="ask"))
    policy.add_rule(PermissionRule(tool_pattern="write_file", action="ask"))
    policy.add_rule(PermissionRule(tool_pattern="edit_file", action="ask"))
    # Default: ask for anything else
    policy.add_rule(PermissionRule(tool_pattern=".*", action="ask"))
    return policy


def _make_trust_policy() -> PermissionPolicy:
    """Trust policy: allow everything without asking."""
    policy = PermissionPolicy()
    policy.add_rule(PermissionRule(tool_pattern=".*", action="allow"))
    return policy


def _make_plan_policy() -> PermissionPolicy:
    """Plan policy: read-only mode, deny all writes and execution."""
    policy = PermissionPolicy()
    # Allow read-only tools
    policy.add_rule(PermissionRule(tool_pattern="read_file", action="allow"))
    policy.add_rule(PermissionRule(tool_pattern="search_files", action="allow"))
    policy.add_rule(PermissionRule(tool_pattern="web_search", action="allow"))
    policy.add_rule(PermissionRule(tool_pattern="web_fetch", action="allow"))
    policy.add_rule(PermissionRule(tool_pattern="think", action="allow"))
    policy.add_rule(PermissionRule(tool_pattern="dispatch", action="allow"))
    # Deny everything else
    policy.add_rule(PermissionRule(tool_pattern=".*", action="deny"))
    return policy


def _make_strict_policy() -> PermissionPolicy:
    """Strict policy: ask for everything, even reads."""
    policy = PermissionPolicy()
    policy.add_rule(PermissionRule(tool_pattern="think", action="allow"))
    policy.add_rule(PermissionRule(tool_pattern=".*", action="ask"))
    return policy


BUILTIN_POLICIES: dict[str, PermissionPolicy] = {
    "default": _make_default_policy(),
    "trust": _make_trust_policy(),
    "plan": _make_plan_policy(),
    "strict": _make_strict_policy(),
}


def get_builtin_policy(name: str) -> PermissionPolicy | None:
    """Get a builtin policy by name.

    Available policies: "default", "trust", "plan", "strict".

    Returns:
        A copy of the policy, or None if not found.
    """
    template = BUILTIN_POLICIES.get(name)
    if template is None:
        return None
    # Return a fresh copy so callers can modify without affecting the template
    policy = PermissionPolicy()
    policy.load_from_config(template.to_config())
    return policy
