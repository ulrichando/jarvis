"""Branch command - Create a branch of the current conversation."""

from __future__ import annotations

command = {
    "type": "local",
    "name": "branch",
    "aliases": ["fork"],
    "description": "Create a branch of the current conversation at this point",
    "argument_hint": "[name]",
}
