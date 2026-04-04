"""JARVIS Commands — unified slash command system.

Combines:
  - Decorator-based handlers (src/commands/handlers/*.py via @command)
  - Dict-based command modules (src/commands/<name>/ with command dicts)

Importing this module registers all commands.
Use `registry` to dispatch commands.

Usage:
    from src.commands import registry
    result = await registry.dispatch("help", ctx)
"""

from src.commands.registry import registry, CommandContext, CommandResult, PermLevel

# Import handlers to register all commands via @command decorators
import src.commands.handlers  # noqa: F401

# Auto-discover and register dict-based command modules
from src.commands.loader import register_in_brain_registry as _load_dict_commands
_load_dict_commands()

__all__ = ["registry", "CommandContext", "CommandResult", "PermLevel"]
