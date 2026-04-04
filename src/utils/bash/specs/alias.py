"""Alias command spec."""

from __future__ import annotations

from ..registry import Argument, CommandSpec

alias = CommandSpec(
    name="alias",
    description="Create or list command aliases",
    args=[
        Argument(
            name="definition",
            description="Alias definition in the form name=value",
            is_optional=True,
            is_variadic=True,
        )
    ],
)
