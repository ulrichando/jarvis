"""JARVIS Commands — structured slash command system.

Importing this module registers all 91 commands (85 visible + 6 hidden).
Use `registry` to dispatch commands.

Usage:
    from src.commands_brain import registry
    result = await registry.dispatch("help", ctx)
"""

from src.commands_brain.registry import registry, CommandContext, CommandResult, PermLevel

# Import handlers to register all commands via @command decorators
import src.commands_brain.handlers  # noqa: F401

__all__ = ["registry", "CommandContext", "CommandResult", "PermLevel"]
