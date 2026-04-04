"""Exit command - Exit the REPL."""

from __future__ import annotations

command = {
    "type": "local",
    "name": "exit",
    "aliases": ["quit"],
    "description": "Exit the REPL",
    "immediate": True,
}
