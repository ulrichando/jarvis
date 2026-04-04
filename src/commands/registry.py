"""JARVIS Command Registry — structured slash command system.

All slash commands are registered here with metadata for:
- Permission checking (READ_ONLY, STANDARD, FULL, DANGEROUS)
- Category grouping for /help display
- Alias resolution
- Structured dispatch with context
"""

import logging
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Callable, Any

log = logging.getLogger("jarvis.commands")


class PermLevel(IntEnum):
    READ_ONLY = 0
    STANDARD = 1
    FULL = 2
    DANGEROUS = 3


@dataclass
class CommandContext:
    """Context passed to every command handler."""
    brain: Any = None           # The Brain instance
    session_mgr: Any = None     # SessionManager
    raw_input: str = ""         # Full user input
    args: str = ""              # Arguments after command name
    mode: str = "normal"        # Current mode


@dataclass
class CommandResult:
    """Structured result from a command handler."""
    text: str = ""              # Text to display
    success: bool = True
    action: str = ""            # Special action: "clear", "exit", "minimize", etc.
    data: dict = field(default_factory=dict)  # Structured data for programmatic use


@dataclass
class CommandDef:
    """Definition of a registered command."""
    name: str                   # "/help"
    aliases: list[str]          # ["/h", "/?"]
    description: str            # One-line help text
    usage: str                  # "/help [--all] [command]"
    category: str               # "core", "session", "memory", etc.
    handler: Callable           # async def handler(ctx) -> CommandResult
    permission: PermLevel = PermLevel.STANDARD
    hidden: bool = False        # Hidden from /help, shown with --all


# Category display order and labels
CATEGORIES = [
    ("core", "Core"),
    ("session", "Session & History"),
    ("memory", "Memory"),
    ("agent", "Agent & Team"),
    ("task", "Task Management"),
    ("mcp", "Tools & MCP"),
    ("plugin", "Plugins & Skills"),
    ("git", "Git & Code"),
    ("security", "System & Security"),
]


class CommandRegistry:
    """Central registry for all slash commands."""

    def __init__(self):
        self._commands: dict[str, CommandDef] = {}  # name -> def
        self._aliases: dict[str, str] = {}          # alias -> canonical name

    def register(self, cmd: CommandDef):
        """Register a command definition."""
        name = cmd.name.lstrip("/").lower()
        self._commands[name] = cmd
        for alias in cmd.aliases:
            self._aliases[alias.lstrip("/").lower()] = name

    def resolve(self, name: str) -> CommandDef | None:
        """Resolve a command name or alias to its definition."""
        key = name.lstrip("/").lower()
        if key in self._commands:
            return self._commands[key]
        canonical = self._aliases.get(key)
        if canonical:
            return self._commands.get(canonical)
        return None

    async def dispatch(self, name: str, ctx: CommandContext) -> CommandResult | None:
        """Dispatch a slash command. Returns None if command not found."""
        cmd = self.resolve(name)
        if cmd is None:
            return None

        # Permission check
        from src.permissions import PermissionLevel
        current_level = PermissionLevel.FULL  # default
        if ctx.brain and hasattr(ctx.brain, 'permissions'):
            current_level = ctx.brain.permissions.level

        if current_level < cmd.permission:
            return CommandResult(
                text=f"Permission denied: /{cmd.name} requires {cmd.permission.name}, current level is {PermLevel(current_level).name}",
                success=False,
            )

        try:
            return await cmd.handler(ctx)
        except Exception as e:
            log.error("Command /%s failed: %s", cmd.name, e)
            return CommandResult(text=f"Command error: {e}", success=False)

    def list_commands(self, category: str = None, include_hidden: bool = False) -> list[CommandDef]:
        """List registered commands, optionally filtered."""
        cmds = list(self._commands.values())
        if not include_hidden:
            cmds = [c for c in cmds if not c.hidden]
        if category:
            cmds = [c for c in cmds if c.category == category]
        # Sort by category order, then name
        cat_order = {cat: i for i, (cat, _) in enumerate(CATEGORIES)}
        cmds.sort(key=lambda c: (cat_order.get(c.category, 99), c.name))
        return cmds

    def categories(self) -> list[tuple[str, str]]:
        """Return category slugs and display names."""
        return CATEGORIES.copy()

    def get_help(self, name: str) -> str:
        """Get detailed help for a specific command."""
        cmd = self.resolve(name)
        if not cmd:
            return f"Unknown command: {name}"
        lines = [
            f"/{cmd.name}",
            f"  {cmd.description}",
            f"  Usage: {cmd.usage}",
            f"  Permission: {cmd.permission.name}",
            f"  Category: {cmd.category}",
        ]
        if cmd.aliases:
            lines.append(f"  Aliases: {', '.join('/' + a for a in cmd.aliases)}")
        return "\n".join(lines)

    @property
    def count(self) -> int:
        return len(self._commands)

    def suggest(self, partial: str, limit: int = 5) -> list[CommandDef]:
        """Fuzzy-match a partial command name. Returns best matches."""
        partial = partial.lstrip("/").lower()
        if not partial:
            return []

        scored = []
        for name, cmd in self._commands.items():
            score = _fuzzy_score(partial, name)
            # Also check aliases
            for alias in cmd.aliases:
                alias_score = _fuzzy_score(partial, alias.lstrip("/").lower())
                score = max(score, alias_score)
            if score > 0:
                scored.append((score, cmd))

        # Also check against alias keys
        for alias, canonical in self._aliases.items():
            cmd = self._commands.get(canonical)
            if cmd:
                score = _fuzzy_score(partial, alias)
                if score > 0:
                    # Avoid duplicates
                    if not any(c.name == cmd.name for _, c in scored):
                        scored.append((score, cmd))

        scored.sort(key=lambda x: -x[0])
        return [cmd for _, cmd in scored[:limit]]

    @property
    def visible_count(self) -> int:
        return len([c for c in self._commands.values() if not c.hidden])


def _fuzzy_score(query: str, target: str) -> float:
    """Score how well query matches target (0.0 to 1.0)."""
    if query == target:
        return 1.0
    if target.startswith(query):
        return 0.9
    if query in target:
        return 0.7
    # Token overlap
    q_tokens = set(query)
    t_tokens = set(target)
    overlap = len(q_tokens & t_tokens)
    if overlap == 0:
        return 0.0
    return 0.3 * (overlap / max(len(q_tokens), 1))


# Global registry singleton
registry = CommandRegistry()


def command(
    name: str,
    aliases: list[str] = None,
    description: str = "",
    usage: str = "",
    category: str = "core",
    permission: PermLevel = PermLevel.STANDARD,
    hidden: bool = False,
):
    """Decorator to register a command handler."""
    def decorator(func):
        cmd = CommandDef(
            name=name,
            aliases=aliases or [],
            description=description,
            usage=f"/{name}" if not usage else usage,
            category=category,
            handler=func,
            permission=permission,
            hidden=hidden,
        )
        registry.register(cmd)
        return func
    return decorator
