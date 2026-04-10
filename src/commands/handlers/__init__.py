"""JARVIS Command Handlers — import all handler modules to register commands."""

# Import all handler modules so their @command decorators run at import time.
# Order matters: later imports overwrite earlier ones for duplicate names.
# Extra/extended load first; specialized handlers load after to take priority.

from src.commands.handlers import extra        # noqa: F401  (extra extended commands -- loaded early)
from src.commands.handlers import extended      # noqa: F401  (15 extended commands)
from src.commands.handlers import core          # noqa: F401  (11 commands)
from src.commands.handlers import session       # noqa: F401  (9 commands)
from src.commands.handlers import memory        # noqa: F401  (10 commands)
from src.commands.handlers import agent         # noqa: F401  (12 commands)
from src.commands.handlers import task          # noqa: F401  (10 commands)
from src.commands.handlers import mcp           # noqa: F401  (10 commands)
from src.commands.handlers import plugin        # noqa: F401  (7 commands)
from src.commands.handlers import git           # noqa: F401  (10 commands)
from src.commands.handlers import security      # noqa: F401  (6 commands)
from src.commands.handlers import debug         # noqa: F401  (6 hidden commands)
from src.commands.handlers import review        # noqa: F401  (structured codebase review)
from src.commands.handlers import troubleshoot  # noqa: F401  (code troubleshooting)
from src.commands.handlers import power         # noqa: F401  (shutdown, reboot, sleep, lock)
from src.commands.handlers import insights      # noqa: F401  (insights, security-review, pr-comments)
from src.commands.handlers import remote        # noqa: F401  (remote, IDE, integration commands)
from src.commands.handlers import habit         # noqa: F401  (self-modification, /habit command)
