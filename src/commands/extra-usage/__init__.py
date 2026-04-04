"""Extra-usage command - Configure extra usage when limits are hit."""

from __future__ import annotations

import os

command = {
    "type": "local",
    "name": "extra-usage",
    "description": "Configure extra usage to keep working when limits are hit",
    "is_enabled": lambda: not os.environ.get("DISABLE_EXTRA_USAGE_COMMAND", "").lower() in ("1", "true", "yes"),
}
