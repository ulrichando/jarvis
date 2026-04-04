"""Sandbox command - Toggle sandbox mode."""

from __future__ import annotations

command = {
    "type": "local",
    "name": "sandbox",
    "description": "Toggle sandbox mode for command execution",
    "argument_hint": 'exclude "command pattern"',
    "immediate": True,
}
