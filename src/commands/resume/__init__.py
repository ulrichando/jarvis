"""Resume command - Resume a previous conversation."""

from __future__ import annotations

command = {
    "type": "local",
    "name": "resume",
    "description": "Resume a previous conversation",
    "aliases": ["continue"],
    "argument_hint": "[conversation id or search term]",
}
