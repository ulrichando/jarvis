"""JARVIS Command Handlers — import all handler modules to register commands."""

# Import all handler modules so their @command decorators run at import time
from brain.commands.handlers import core      # noqa: F401  (11 commands)
from brain.commands.handlers import session   # noqa: F401  (9 commands)
from brain.commands.handlers import memory    # noqa: F401  (10 commands)
from brain.commands.handlers import agent     # noqa: F401  (12 commands)
from brain.commands.handlers import task      # noqa: F401  (10 commands)
from brain.commands.handlers import mcp       # noqa: F401  (10 commands)
from brain.commands.handlers import plugin    # noqa: F401  (7 commands)
from brain.commands.handlers import git       # noqa: F401  (10 commands)
from brain.commands.handlers import security  # noqa: F401  (6 commands)
from brain.commands.handlers import debug     # noqa: F401  (6 hidden commands)
from brain.commands.handlers import extra     # noqa: F401  (extra Claude Code commands)
from brain.commands.handlers import review       # noqa: F401  (structured codebase review)
from brain.commands.handlers import troubleshoot # noqa: F401  (code troubleshooting)
