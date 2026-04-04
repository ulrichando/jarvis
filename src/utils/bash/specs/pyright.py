"""Pyright command spec."""

from __future__ import annotations

from ..registry import Argument, CommandOption, CommandSpec

pyright = CommandSpec(
    name="pyright",
    description="Type checker for Python",
    options=[
        CommandOption(name="--help", description="Show help message"),
        CommandOption(name="--version", description="Print pyright version and exit"),
        CommandOption(name="--watch", description="Continue to run and watch for changes"),
        CommandOption(
            name="--project",
            description="Use the configuration file at this location",
            args=[Argument(name="FILE OR DIRECTORY")],
        ),
        CommandOption(name="--outputjson", description="Output results in JSON format"),
        CommandOption(name="--verbose", description="Emit verbose diagnostics"),
        CommandOption(name="--stats", description="Print detailed performance stats"),
    ],
    args=[
        Argument(
            name="files",
            description="Specify files or directories to analyze",
            is_variadic=True,
            is_optional=True,
        )
    ],
)
