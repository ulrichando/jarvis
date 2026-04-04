"""Timeout command spec."""

from __future__ import annotations

from ..registry import Argument, CommandSpec

timeout = CommandSpec(
    name="timeout",
    description="Run a command with a time limit",
    args=[
        Argument(
            name="duration",
            description="Duration to wait before timing out",
        ),
        Argument(
            name="command",
            description="Command to run",
            is_command=True,
        ),
    ],
)
