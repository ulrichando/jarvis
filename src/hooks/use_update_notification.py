"""Update notification when a new version is available."""

from __future__ import annotations

import re
from typing import Optional


def get_semver_part(version: str) -> str:
    """Extract major.minor.patch from a version string."""
    match = re.match(r"(\d+)\.(\d+)\.(\d+)", version)
    if match:
        return f"{match.group(1)}.{match.group(2)}.{match.group(3)}"
    return version


def should_show_update_notification(
    updated_version: str,
    last_notified_semver: Optional[str],
) -> bool:
    """Check if an update notification should be shown."""
    updated_semver = get_semver_part(updated_version)
    return updated_semver != last_notified_semver


class UpdateNotification:
    """Manages update notification state.

    Equivalent to useUpdateNotification React hook.
    """

    def __init__(self, initial_version: str = "0.0.0"):
        self._last_notified = get_semver_part(initial_version)

    def check(self, updated_version: Optional[str]) -> Optional[str]:
        """Check if notification should be shown. Returns semver if yes."""
        if not updated_version:
            return None
        updated_semver = get_semver_part(updated_version)
        if updated_semver != self._last_notified:
            self._last_notified = updated_semver
            return updated_semver
        return None
