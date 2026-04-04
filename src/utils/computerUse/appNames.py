"""Filter and sanitize installed-app data for computer use tool descriptions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

PATH_ALLOWLIST = ["/Applications/", "/System/Applications/"]

NAME_PATTERN_BLOCKLIST = [
    re.compile(r"Helper(?:$|\s\()"),
    re.compile(r"Agent(?:$|\s\()"),
    re.compile(r"Service(?:$|\s\()"),
    re.compile(r"Uninstaller(?:$|\s\()"),
    re.compile(r"Updater(?:$|\s\()"),
    re.compile(r"^\."),
]

ALWAYS_KEEP_BUNDLE_IDS = {
    "com.google.Chrome",
    "com.apple.Safari",
    "org.mozilla.firefox",
    "com.microsoft.edgemac",
}


@dataclass
class InstalledApp:
    bundle_id: str
    display_name: str
    path: str


def filter_apps(
    apps: list[InstalledApp],
    home_dir: Optional[str] = None,
    max_count: int = 100,
) -> list[InstalledApp]:
    """Filter installed apps to relevant ones for computer use."""
    if home_dir is None:
        home_dir = str(Path.home())

    user_apps_path = f"{home_dir}/Applications/"
    allowed_paths = list(PATH_ALLOWLIST) + [user_apps_path]

    result: list[InstalledApp] = []

    for app in apps:
        # Always keep priority apps
        if app.bundle_id in ALWAYS_KEEP_BUNDLE_IDS:
            result.append(app)
            continue

        # Check path allowlist
        if not any(app.path.startswith(p) for p in allowed_paths):
            continue

        # Check name blocklist
        if any(pat.search(app.display_name) for pat in NAME_PATTERN_BLOCKLIST):
            continue

        result.append(app)

    return result[:max_count]


def sanitize_app_name(name: str, max_length: int = 50) -> str:
    """Sanitize an app name for inclusion in tool descriptions."""
    # Remove control characters
    cleaned = re.sub(r"[\x00-\x1f\x7f]", "", name)
    # Truncate
    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length] + "..."
    return cleaned
