"""Privacy level controls for telemetry and nonessential network traffic."""

from __future__ import annotations

import os
from typing import Literal, Optional

PrivacyLevel = Literal["default", "no-telemetry", "essential-traffic"]


def get_privacy_level() -> PrivacyLevel:
    """Get the current privacy level based on environment variables."""
    if os.environ.get("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"):
        return "essential-traffic"
    if os.environ.get("DISABLE_TELEMETRY"):
        return "no-telemetry"
    return "default"


def is_essential_traffic_only() -> bool:
    """True when all nonessential network traffic should be suppressed."""
    return get_privacy_level() == "essential-traffic"


def is_telemetry_disabled() -> bool:
    """True when telemetry/analytics should be suppressed."""
    return get_privacy_level() != "default"


def get_essential_traffic_only_reason() -> Optional[str]:
    """Returns the env var name responsible for essential-traffic restriction."""
    if os.environ.get("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"):
        return "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"
    return None
