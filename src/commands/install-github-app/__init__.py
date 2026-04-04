"""Install-github-app command - Set up Claude GitHub Actions for a repository."""

from __future__ import annotations

import os

command = {
    "type": "local",
    "name": "install-github-app",
    "description": "Set up Claude GitHub Actions for a repository",
    "availability": ["claude-ai", "console"],
    "is_enabled": lambda: not os.environ.get("DISABLE_INSTALL_GITHUB_APP_COMMAND", "").lower() in ("1", "true", "yes"),
}
