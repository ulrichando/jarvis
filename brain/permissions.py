"""JARVIS Permission System — role-based access control for tool execution.

Inspired by claw-code's permission hierarchy:
- ReadOnly: Can only read files, search, browse web
- Standard: Can read + write files, run safe commands
- Full: Unrestricted access (default for Ulrich)
- DangerousFullAccess: Bypasses all safety checks

Permissions are checked before tool execution in the agent loop.

Extended with Claude Code-style permission pattern matching:
- PermissionMode: global operation modes (default, bypass, plan, etc.)
- PermissionRule + PermissionMatcher: glob-pattern rules per tool/arg
- Denial tracking: auto-stop-asking after repeated denials
- Rule loading from .jarvis/settings.json and ~/.jarvis/permissions.yaml
"""

from __future__ import annotations

import fnmatch
import json
import logging
import re
from enum import IntEnum, Enum
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from brain.config import JARVIS_HOME

log = logging.getLogger("jarvis.permissions")


# ---------------------------------------------------------------------------
# Original permission levels (unchanged)
# ---------------------------------------------------------------------------

class PermissionLevel(IntEnum):
    READ_ONLY = 0
    STANDARD = 1
    FULL = 2
    DANGEROUS_FULL = 3


@dataclass
class ToolPermission:
    """Permission requirements for a single tool."""
    tool_name: str
    min_level: PermissionLevel = PermissionLevel.STANDARD
    requires_confirmation: bool = False
    description: str = ""


# ---------------------------------------------------------------------------
# Permission modes
# ---------------------------------------------------------------------------

class PermissionMode(Enum):
    """Global operation mode controlling permission behavior."""
    DEFAULT = "default"          # ask on each sensitive operation
    BYPASS = "bypass"            # allow all (dev mode)
    ACCEPT_EDITS = "accept_edits"  # auto-accept file edits only
    DENY_ALL = "deny_all"       # deny everything not in allowlist
    PLAN = "plan"               # read-only mode (no writes)


# ---------------------------------------------------------------------------
# Permission rules & pattern matching
# ---------------------------------------------------------------------------

@dataclass
class PermissionRule:
    """A single permission rule matching tool calls by name and arg pattern."""
    tool_name: str              # tool name or "*" wildcard
    rule_content: str = ""      # glob pattern for primary arg (e.g. "git *")
    behavior: str = "allow"     # "allow", "deny", or "ask"
    source: str = "user"        # "user", "project", "session", "cli"

    @property
    def specificity(self) -> int:
        """Higher = more specific. Used for rule priority."""
        score = 0
        if self.tool_name != "*":
            score += 2
        if self.rule_content:
            score += 1
        return score


# Map tool names to the arg key that holds the "primary" matchable value.
_PRIMARY_ARG_KEYS: dict[str, str] = {
    "bash": "command",
    "edit_file": "path",
    "write_file": "path",
    "read_file": "path",
    "search_files": "pattern",
}


def _glob_to_regex(pattern: str) -> re.Pattern:
    """Convert a glob-style pattern (with * wildcard) to a compiled regex."""
    return re.compile(fnmatch.translate(pattern), re.DOTALL)


class PermissionMatcher:
    """Evaluates tool calls against an ordered set of permission rules.

    Rules are matched by specificity: tool+content > tool-only > wildcard.
    """

    def __init__(self, rules: list[PermissionRule] | None = None):
        self._rules: list[PermissionRule] = list(rules) if rules else []

    # -- rule management ----------------------------------------------------

    def add_rule(self, rule: PermissionRule) -> None:
        self._rules.append(rule)

    def remove_rule(self, tool_name: str, rule_content: str = "") -> bool:
        """Remove the first rule matching (tool_name, rule_content). Returns True if found."""
        for i, r in enumerate(self._rules):
            if r.tool_name == tool_name and r.rule_content == rule_content:
                self._rules.pop(i)
                return True
        return False

    @property
    def rules(self) -> list[PermissionRule]:
        return list(self._rules)

    # -- matching -----------------------------------------------------------

    def check(self, tool_name: str, tool_args: dict) -> tuple[str, str]:
        """Check a tool call against all rules.

        Returns (behavior, reason) where behavior is "allow", "deny", or "ask".

        Matching logic:
        1. Gather all rules whose tool_name matches (exact or "*").
        2. For each, check rule_content against the tool's primary arg.
        3. Most specific matching rule wins.
        4. If nothing matches, return ("ask", "no matching rule").
        """
        candidates: list[tuple[int, PermissionRule]] = []

        for rule in self._rules:
            # Tool name match: exact or wildcard
            if rule.tool_name != "*" and rule.tool_name != tool_name:
                continue

            # Content match
            if rule.rule_content:
                primary_value = self._extract_primary_arg(tool_name, tool_args)
                if primary_value is None:
                    continue
                pat = _glob_to_regex(rule.rule_content)
                if not pat.match(primary_value):
                    continue

            candidates.append((rule.specificity, rule))

        if not candidates:
            return ("ask", "no matching rule")

        # Sort descending by specificity; pick best
        candidates.sort(key=lambda x: x[0], reverse=True)
        best = candidates[0][1]
        reason = f"matched rule: {best.behavior}:{best.tool_name}"
        if best.rule_content:
            reason += f"({best.rule_content})"
        reason += f" [source={best.source}]"
        return (best.behavior, reason)

    @staticmethod
    def _extract_primary_arg(tool_name: str, tool_args: dict) -> str | None:
        """Get the primary matchable string from tool args."""
        key = _PRIMARY_ARG_KEYS.get(tool_name)
        if key:
            return tool_args.get(key)
        # Fallback: try common keys
        for fallback in ("command", "path", "pattern", "query"):
            if fallback in tool_args:
                return tool_args[fallback]
        return None

    # -- parsing ------------------------------------------------------------

    @staticmethod
    def parse_rule_string(rule_str: str, source: str = "user") -> PermissionRule:
        """Parse a compact rule string into a PermissionRule.

        Formats:
          "allow:bash(git *)"      -> PermissionRule("bash", "git *", "allow")
          "deny:bash(rm -rf *)"    -> PermissionRule("bash", "rm -rf *", "deny")
          "allow:read_file"        -> PermissionRule("read_file", "", "allow")
          "deny:*"                 -> PermissionRule("*", "", "deny")
          "ask:write_file(/etc/*)" -> PermissionRule("write_file", "/etc/*", "ask")
        """
        rule_str = rule_str.strip()

        # Split on first ':'
        if ":" not in rule_str:
            raise ValueError(f"Invalid rule string (missing behavior prefix): {rule_str!r}")

        behavior, rest = rule_str.split(":", 1)
        behavior = behavior.lower().strip()
        if behavior not in ("allow", "deny", "ask"):
            raise ValueError(f"Invalid behavior {behavior!r}, must be allow/deny/ask")

        rest = rest.strip()

        # Check for content pattern in parens
        paren_match = re.match(r'^([^(]+)\((.+)\)$', rest)
        if paren_match:
            tool_name = paren_match.group(1).strip()
            rule_content = paren_match.group(2).strip()
        else:
            tool_name = rest.strip()
            rule_content = ""

        return PermissionRule(
            tool_name=tool_name,
            rule_content=rule_content,
            behavior=behavior,
            source=source,
        )


# ---------------------------------------------------------------------------
# Permission manager (extended from original)
# ---------------------------------------------------------------------------

class PermissionManager:
    """Manages tool execution permissions.

    Combines the original level-based system with pattern-matching rules,
    operation modes, and denial tracking.
    """

    def __init__(
        self,
        level: PermissionLevel = PermissionLevel.FULL,
        mode: PermissionMode = PermissionMode.DEFAULT,
    ):
        self.level = level
        self.mode = mode
        self._denied_tools: set[str] = set()
        self._denied_prefixes: list[str] = []
        self._tool_permissions: dict[str, ToolPermission] = {}
        self._matcher = PermissionMatcher()
        self._denial_counts: dict[str, int] = {}
        self._register_defaults()

    def _register_defaults(self):
        """Register default tool permission requirements."""
        # Read-only tools
        for tool in ("read_file", "search_files", "web_search", "web_fetch", "think"):
            self._tool_permissions[tool] = ToolPermission(tool, PermissionLevel.READ_ONLY)

        # Standard tools
        for tool in ("write_file", "edit_file"):
            self._tool_permissions[tool] = ToolPermission(tool, PermissionLevel.STANDARD)

        # Full access tools
        self._tool_permissions["bash"] = ToolPermission("bash", PermissionLevel.STANDARD, requires_confirmation=False)
        self._tool_permissions["dispatch"] = ToolPermission("dispatch", PermissionLevel.STANDARD)

    # -- core check (original + extensions) ---------------------------------

    def check(self, tool_name: str, tool_args: dict = None) -> tuple[bool, str]:
        """Check if a tool call is allowed under current permissions.

        Returns (allowed, reason).

        Evaluation order:
        1. Global mode short-circuits (BYPASS, DENY_ALL, PLAN, ACCEPT_EDITS)
        2. Explicit denials (tool names / prefixes)
        3. Permission level check
        4. Pattern-matching rules via PermissionMatcher
        5. Default: allowed
        """
        if tool_args is None:
            tool_args = {}

        # --- Mode-based short circuits ---
        if self.mode == PermissionMode.BYPASS:
            return True, "bypass mode"

        if self.mode == PermissionMode.DENY_ALL:
            # Only pass if matcher explicitly allows
            behavior, reason = self._matcher.check(tool_name, tool_args)
            if behavior == "allow":
                return True, f"deny_all mode, but allowlisted: {reason}"
            return False, "deny_all mode: not in allowlist"

        if self.mode == PermissionMode.PLAN:
            # Read-only: only allow tools at READ_ONLY level
            perm = self._tool_permissions.get(tool_name)
            if perm and perm.min_level <= PermissionLevel.READ_ONLY:
                return True, "plan mode: read-only tool allowed"
            return False, f"plan mode: tool '{tool_name}' requires write access"

        if self.mode == PermissionMode.ACCEPT_EDITS:
            # Auto-accept file edit tools; others go through normal flow
            if tool_name in ("write_file", "edit_file"):
                return True, "accept_edits mode: file edit auto-accepted"

        # --- Explicit denials ---
        if tool_name in self._denied_tools:
            return False, f"Tool '{tool_name}' is explicitly denied"

        for prefix in self._denied_prefixes:
            if tool_name.startswith(prefix):
                return False, f"Tool '{tool_name}' matches denied prefix '{prefix}'"

        # --- Permission level ---
        perm = self._tool_permissions.get(tool_name)
        if perm and self.level < perm.min_level:
            return False, f"Tool '{tool_name}' requires {perm.min_level.name}, current level is {self.level.name}"

        # --- Pattern-matching rules ---
        if self._matcher.rules:
            behavior, reason = self._matcher.check(tool_name, tool_args)
            if behavior == "allow":
                return True, reason
            elif behavior == "deny":
                return False, reason
            # "ask" falls through to default allow

        return True, ""

    # -- original methods (unchanged) ---------------------------------------

    def deny_tool(self, tool_name: str):
        self._denied_tools.add(tool_name)

    def deny_prefix(self, prefix: str):
        self._denied_prefixes.append(prefix)

    def allow_tool(self, tool_name: str):
        self._denied_tools.discard(tool_name)

    def set_level(self, level: PermissionLevel):
        self.level = level

    def get_allowed_tools(self, available_tools: list[str]) -> list[str]:
        """Filter a list of tool names to only those currently allowed."""
        return [t for t in available_tools if self.check(t)[0]]

    # -- mode management ----------------------------------------------------

    def set_mode(self, mode: PermissionMode) -> None:
        self.mode = mode
        log.info("Permission mode set to %s", mode.value)

    # -- pattern rule management -------------------------------------------

    @property
    def matcher(self) -> PermissionMatcher:
        return self._matcher

    def add_rule(self, rule: PermissionRule) -> None:
        self._matcher.add_rule(rule)

    def add_rule_string(self, rule_str: str, source: str = "user") -> PermissionRule:
        """Parse and add a rule from a compact string. Returns the created rule."""
        rule = PermissionMatcher.parse_rule_string(rule_str, source=source)
        self._matcher.add_rule(rule)
        return rule

    def remove_rule(self, tool_name: str, rule_content: str = "") -> bool:
        return self._matcher.remove_rule(tool_name, rule_content)

    # -- denial tracking ---------------------------------------------------

    def record_denial(self, tool_name: str) -> None:
        """Record that a tool call was denied (by user or rule)."""
        self._denial_counts[tool_name] = self._denial_counts.get(tool_name, 0) + 1

    def get_denial_count(self, tool_name: str) -> int:
        return self._denial_counts.get(tool_name, 0)

    def should_stop_asking(self, tool_name: str, threshold: int = 3) -> bool:
        """True if the tool has been denied >= threshold times this session."""
        return self.get_denial_count(tool_name) >= threshold

    def reset_denial_counts(self) -> None:
        self._denial_counts.clear()

    # -- rule loading from config files ------------------------------------

    def load_rules_from_settings(self, project_dir: Path | str | None = None) -> int:
        """Load permission rules from project and user config files.

        Sources (in order, later sources can override earlier):
        1. .jarvis/settings.json  -> "permissions" key (list of rule strings)
        2. ~/.jarvis/permissions.yaml -> list of rule dicts or strings

        Returns the number of rules loaded.
        """
        loaded = 0

        # 1. Project settings
        if project_dir is not None:
            settings_path = Path(project_dir) / ".jarvis" / "settings.json"
            loaded += self._load_from_settings_json(settings_path)

        # 2. User-level permissions.yaml
        yaml_path = JARVIS_HOME / "permissions.yaml"
        loaded += self._load_from_permissions_yaml(yaml_path)

        if loaded:
            log.info("Loaded %d permission rules from config files", loaded)
        return loaded

    def _load_from_settings_json(self, path: Path) -> int:
        """Load rules from .jarvis/settings.json 'permissions' key."""
        if not path.is_file():
            return 0
        try:
            data = json.loads(path.read_text())
            rules_raw = data.get("permissions", [])
            if not isinstance(rules_raw, list):
                log.warning("permissions key in %s is not a list, skipping", path)
                return 0
            count = 0
            for entry in rules_raw:
                try:
                    if isinstance(entry, str):
                        rule = PermissionMatcher.parse_rule_string(entry, source="project")
                    elif isinstance(entry, dict):
                        rule = PermissionRule(
                            tool_name=entry.get("tool", entry.get("tool_name", "*")),
                            rule_content=entry.get("content", entry.get("rule_content", "")),
                            behavior=entry.get("behavior", "allow"),
                            source="project",
                        )
                    else:
                        continue
                    self._matcher.add_rule(rule)
                    count += 1
                except (ValueError, KeyError) as exc:
                    log.warning("Skipping invalid permission rule in %s: %s", path, exc)
            return count
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Failed to read %s: %s", path, exc)
            return 0

    def _load_from_permissions_yaml(self, path: Path) -> int:
        """Load rules from ~/.jarvis/permissions.yaml."""
        if not path.is_file():
            return 0
        try:
            import yaml
        except ImportError:
            # Try loading as simple line-based format if PyYAML not available
            return self._load_yaml_fallback(path)

        try:
            data = yaml.safe_load(path.read_text())
            if not isinstance(data, list):
                if isinstance(data, dict):
                    data = data.get("rules", data.get("permissions", []))
                if not isinstance(data, list):
                    log.warning("permissions.yaml at %s has unexpected format", path)
                    return 0

            count = 0
            for entry in data:
                try:
                    if isinstance(entry, str):
                        rule = PermissionMatcher.parse_rule_string(entry, source="user")
                    elif isinstance(entry, dict):
                        rule = PermissionRule(
                            tool_name=entry.get("tool", entry.get("tool_name", "*")),
                            rule_content=entry.get("content", entry.get("rule_content", "")),
                            behavior=entry.get("behavior", "allow"),
                            source="user",
                        )
                    else:
                        continue
                    self._matcher.add_rule(rule)
                    count += 1
                except (ValueError, KeyError) as exc:
                    log.warning("Skipping invalid permission rule in %s: %s", path, exc)
            return count
        except (OSError, yaml.YAMLError) as exc:
            log.warning("Failed to read %s: %s", path, exc)
            return 0

    def _load_yaml_fallback(self, path: Path) -> int:
        """Fallback: read permissions.yaml as line-based rule strings when PyYAML is unavailable."""
        count = 0
        try:
            for line in path.read_text().splitlines():
                line = line.strip().lstrip("- ")
                if not line or line.startswith("#"):
                    continue
                try:
                    rule = PermissionMatcher.parse_rule_string(line, source="user")
                    self._matcher.add_rule(rule)
                    count += 1
                except ValueError:
                    pass
            return count
        except OSError:
            return 0

    # -- summary -----------------------------------------------------------

    def summary(self) -> dict:
        return {
            "level": self.level.name,
            "mode": self.mode.value,
            "denied_tools": sorted(self._denied_tools),
            "denied_prefixes": self._denied_prefixes,
            "rules": [
                {
                    "tool": r.tool_name,
                    "content": r.rule_content,
                    "behavior": r.behavior,
                    "source": r.source,
                }
                for r in self._matcher.rules
            ],
            "denial_counts": dict(self._denial_counts),
        }
