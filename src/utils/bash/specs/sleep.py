"""Sleep command spec."""

from __future__ import annotations

from ..registry import Argument, CommandSpec

sleep = CommandSpec(
    name="sleep",
    description="Delay for a specified amount of time",
    args=[
        Argument(
            name="duration",
            description="Duration to sleep (seconds or with suffix like 5s, 2m, 1h)",
        )
    ],
)
