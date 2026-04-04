"""Bridge command - Connect terminal for remote-control sessions."""

from __future__ import annotations

command = {
    "type": "local",
    "name": "remote-control",
    "aliases": ["rc"],
    "description": "Connect this terminal for remote-control sessions",
    "argument_hint": "[name]",
    "immediate": True,
}
