"""Shell snapshot utilities for capturing and restoring shell state."""

from __future__ import annotations

import logging
import os
import shlex
import subprocess
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

SNAPSHOT_CREATION_TIMEOUT = 10000  # 10 seconds


@dataclass
class ShellSnapshot:
    """Represents a captured snapshot of the shell environment."""

    env_vars: dict[str, str]
    cwd: str
    aliases: dict[str, str]

    @classmethod
    def capture(cls) -> ShellSnapshot:
        """Capture the current shell environment."""
        return cls(
            env_vars=dict(os.environ),
            cwd=os.getcwd(),
            aliases={},
        )

    def restore_env(self) -> None:
        """Restore environment variables from snapshot."""
        for key, value in self.env_vars.items():
            os.environ[key] = value
