"""Nohup command spec."""

from __future__ import annotations

from ..registry import Argument, CommandSpec

nohup = CommandSpec(
    name="nohup",
    description="Run a command immune to hangups",
    args=[
        Argument(
            name="command",
            description="Command to run with nohup",
            is_command=True,
        )
    ],
)
