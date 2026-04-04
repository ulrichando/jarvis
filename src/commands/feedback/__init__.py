"""Feedback command - Submit feedback about JARVIS."""

from __future__ import annotations

import os

command = {
    "type": "local",
    "name": "feedback",
    "aliases": ["bug"],
    "description": "Submit feedback about JARVIS",
    "argument_hint": "[report]",
    "is_enabled": lambda: not os.environ.get("DISABLE_FEEDBACK_COMMAND", "").lower() in ("1", "true", "yes"),
}
