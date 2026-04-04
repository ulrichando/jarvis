"""Bridge status utilities shared between CLI and React renderers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Optional

StatusState = Literal["idle", "attached", "titled", "reconnecting", "failed"]

TOOL_DISPLAY_EXPIRY_MS = 30_000
SHIMMER_INTERVAL_MS = 150


def timestamp() -> str:
    """Get current time as HH:MM:SS string."""
    now = datetime.now()
    return f"{now.hour:02d}:{now.minute:02d}:{now.second:02d}"


def format_duration(ms: float) -> str:
    """Format milliseconds as human-readable duration."""
    if ms < 60_000:
        return f"{round(ms / 1000)}s"
    m = int(ms // 60_000)
    s = round((ms % 60_000) / 1000)
    return f"{m}m {s}s" if s > 0 else f"{m}m"


def truncate_prompt(text: str, max_width: int) -> str:
    """Truncate text to max_width characters."""
    if len(text) <= max_width:
        return text
    return text[:max_width - 1] + "\u2026"


def abbreviate_activity(summary: str) -> str:
    """Abbreviate a tool activity summary for the trail display."""
    return truncate_prompt(summary, 30)


def build_bridge_connect_url(environment_id: str, ingress_url: Optional[str] = None) -> str:
    """Build the connect URL shown when the bridge is idle."""
    base_url = ingress_url or "http://localhost:8765"
    return f"{base_url}/remote?bridge={environment_id}"


def build_bridge_session_url(
    session_id: str, environment_id: str, ingress_url: Optional[str] = None,
) -> str:
    """Build the session URL shown when a session is attached."""
    base_url = ingress_url or "http://localhost:8765"
    return f"{base_url}/remote/session/{session_id}?bridge={environment_id}"


def compute_glimmer_index(tick: int, message_width: int) -> int:
    """Compute the glimmer index for a reverse-sweep shimmer animation."""
    cycle_length = message_width + 20
    return message_width + 10 - (tick % cycle_length)


def compute_shimmer_segments(text: str, glimmer_index: int) -> dict[str, str]:
    """Split text into three segments by visual column position for shimmer rendering."""
    message_width = len(text)
    shimmer_start = glimmer_index - 1
    shimmer_end = glimmer_index + 1

    if shimmer_start >= message_width or shimmer_end < 0:
        return {"before": text, "shimmer": "", "after": ""}

    clamped_start = max(0, shimmer_start)
    before = text[:clamped_start]
    shimmer = text[clamped_start:min(shimmer_end + 1, message_width)]
    after = text[min(shimmer_end + 1, message_width):]
    return {"before": before, "shimmer": shimmer, "after": after}


@dataclass
class BridgeStatusInfo:
    label: str
    color: str


def get_bridge_status(
    error: Optional[str] = None,
    connected: bool = False,
    session_active: bool = False,
    reconnecting: bool = False,
) -> BridgeStatusInfo:
    """Derive a status label and color from the bridge connection state."""
    if error:
        return BridgeStatusInfo(label="Remote Control failed", color="error")
    if reconnecting:
        return BridgeStatusInfo(label="Remote Control reconnecting", color="warning")
    if session_active or connected:
        return BridgeStatusInfo(label="Remote Control active", color="success")
    return BridgeStatusInfo(label="Remote Control connecting\u2026", color="warning")


def build_idle_footer_text(url: str) -> str:
    return f"Connect remotely at {url}"


def build_active_footer_text(url: str) -> str:
    return f"Remote session active at {url}"


FAILED_FOOTER_TEXT = "Something went wrong, please try again"


def wrap_with_osc8_link(text: str, url: str) -> str:
    """Wrap text in an OSC 8 terminal hyperlink."""
    return f"\x1b]8;;{url}\x07{text}\x1b]8;;\x07"
