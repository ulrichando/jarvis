"""Time command spec."""

from __future__ import annotations

from ..registry import Argument, CommandSpec

time = CommandSpec(
    name="time",
    description="Time a command",
    args=[
        Argument(
            name="command",
            description="Command to time",
            is_command=True,
        )
    ],
)
