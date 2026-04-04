"""Doctor command - Diagnose and verify installation and settings."""

from __future__ import annotations

import os

command = {
    "type": "local",
    "name": "doctor",
    "description": "Diagnose and verify your JARVIS installation and settings",
    "is_enabled": lambda: not os.environ.get("DISABLE_DOCTOR_COMMAND", "").lower() in ("1", "true", "yes"),
}
