"""JARVIS Command Handlers — import all handler modules to register commands."""

# Import all handler modules so their @command decorators run at import time.
# Order matters: later imports overwrite earlier ones for duplicate names.
# Extra/extended load first; specialized handlers load after to take priority.

from src.commands_brain.handlers import extra        # noqa: F401  (extra Claude Code commands -- loaded early)
from src.commands_brain.handlers import extended      # noqa: F401  (15 commands from converted Claude Code set)
from src.commands_brain.handlers import core          # noqa: F401  (11 commands)
from src.commands_brain.handlers import session       # noqa: F401  (9 commands)
from src.commands_brain.handlers import memory        # noqa: F401  (10 commands)
from src.commands_brain.handlers import agent         # noqa: F401  (12 commands)
from src.commands_brain.handlers import task          # noqa: F401  (10 commands)
from src.commands_brain.handlers import mcp           # noqa: F401  (10 commands)
from src.commands_brain.handlers import plugin        # noqa: F401  (7 commands)
from src.commands_brain.handlers import git           # noqa: F401  (10 commands)
from src.commands_brain.handlers import security      # noqa: F401  (6 commands)
from src.commands_brain.handlers import debug         # noqa: F401  (6 hidden commands)
from src.commands_brain.handlers import review        # noqa: F401  (structured codebase review)
from src.commands_brain.handlers import troubleshoot  # noqa: F401  (code troubleshooting)
from src.commands_brain.handlers import power         # noqa: F401  (shutdown, reboot, sleep, lock)
from src.commands_brain.handlers import insights      # noqa: F401  (insights, security-review, pr-comments)
from src.commands_brain.handlers import remote        # noqa: F401  (remote, IDE, integration commands)
