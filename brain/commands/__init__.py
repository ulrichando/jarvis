"""JARVIS Commands — structured slash command system.

Importing this module registers all 91 commands (85 visible + 6 hidden).
Use `registry` to dispatch commands.

Usage:
    from brain.commands import registry
    result = await registry.dispatch("help", ctx)
"""

from brain.commands.registry import registry, CommandContext, CommandResult, PermLevel

# Import handlers to register all commands via @command decorators
import brain.commands.handlers  # noqa: F401

__all__ = ["registry", "CommandContext", "CommandResult", "PermLevel"]
