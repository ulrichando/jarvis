"""Command spec registry for bash commands."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Argument:
    name: str = ""
    description: str = ""
    is_dangerous: bool = False
    is_variadic: bool = False
    is_optional: bool = False
    is_command: bool = False
    is_module: bool = False
    is_script: bool = False


@dataclass
class CommandOption:
    name: str = ""
    description: str = ""
    args: list[Argument] = field(default_factory=list)
    is_required: bool = False


@dataclass
class CommandSpec:
    name: str
    description: str = ""
    subcommands: list[CommandSpec] = field(default_factory=list)
    args: list[Argument] = field(default_factory=list)
    options: list[CommandOption] = field(default_factory=list)


# Built-in specs
_specs: list[CommandSpec] = []


async def get_command_spec(command: str) -> Optional[CommandSpec]:
    """Get the spec for a command."""
    for spec in _specs:
        if spec.name == command:
            return spec
    return None
