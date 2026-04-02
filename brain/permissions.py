"""JARVIS Permission System — role-based access control for tool execution.

Inspired by claw-code's permission hierarchy:
- ReadOnly: Can only read files, search, browse web
- Standard: Can read + write files, run safe commands
- Full: Unrestricted access (default for Ulrich)
- DangerousFullAccess: Bypasses all safety checks

Permissions are checked before tool execution in the agent loop.
"""

from enum import IntEnum
from dataclasses import dataclass, field


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


class PermissionManager:
    """Manages tool execution permissions."""

    def __init__(self, level: PermissionLevel = PermissionLevel.FULL):
        self.level = level
        self._denied_tools: set[str] = set()
        self._denied_prefixes: list[str] = []
        self._tool_permissions: dict[str, ToolPermission] = {}
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

    def check(self, tool_name: str, tool_args: dict = None) -> tuple[bool, str]:
        """Check if a tool call is allowed under current permissions.

        Returns (allowed, reason).
        """
        # Check explicit denials
        if tool_name in self._denied_tools:
            return False, f"Tool '{tool_name}' is explicitly denied"

        for prefix in self._denied_prefixes:
            if tool_name.startswith(prefix):
                return False, f"Tool '{tool_name}' matches denied prefix '{prefix}'"

        # Check permission level
        perm = self._tool_permissions.get(tool_name)
        if perm and self.level < perm.min_level:
            return False, f"Tool '{tool_name}' requires {perm.min_level.name}, current level is {self.level.name}"

        return True, ""

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

    def summary(self) -> dict:
        return {
            "level": self.level.name,
            "denied_tools": sorted(self._denied_tools),
            "denied_prefixes": self._denied_prefixes,
        }
