"""Srun (SLURM) command spec."""

from __future__ import annotations

from ..registry import Argument, CommandOption, CommandSpec

srun = CommandSpec(
    name="srun",
    description="Run a command on SLURM cluster nodes",
    options=[
        CommandOption(
            name="-n",
            description="Number of tasks",
            args=[Argument(name="count", description="Number of tasks to run")],
        ),
        CommandOption(
            name="-N",
            description="Number of nodes",
            args=[Argument(name="count", description="Number of nodes to allocate")],
        ),
    ],
    args=[
        Argument(
            name="command",
            description="Command to run on the cluster",
            is_command=True,
        )
    ],
)
