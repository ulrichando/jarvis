"""Ephemeral newest-frame cache + vision gate for the computer_use vision-feedback
loop (Web-Nav P2a). Standalone — no livekit/providers imports at module load (they
are lazy, inside functions). JarvisAgent.llm_node reads this to inject the
post-action screen into the per-generation chat_ctx copy.
"""
from __future__ import annotations
import base64
import io
import os
import time
from collections import deque
from typing import Optional

_VISION_PREFIXES_DEFAULT = ("claude-", "gpt-4o", "gpt-4.1", "gemini-")
_DEFAULT_TTL_S = 20.0
_MAX_DOWNSCALE_PX = 1280
_TRAIL_MAXLEN = 3

_latest: Optional[dict] = None
_recent: "deque[str]" = deque(maxlen=_TRAIL_MAXLEN)


def publish_capture(*, png_b64: Optional[str], width, height,
                    action_label: str = "capture", _now: Optional[float] = None) -> None:
    """Store the newest screenshot frame (overwrites any prior). No-op if no png."""
    global _latest
    if not png_b64:
        return
    ts = _now if _now is not None else time.monotonic()
    _latest = {"png_b64": png_b64, "width": int(width or 0), "height": int(height or 0),
               "action_label": action_label or "capture", "ts": ts}


def record_action(label: str) -> None:
    """Append a short action label to the recent-actions trail (cheap context)."""
    if label:
        _recent.append(label)


def take_current(ttl_s: float = _DEFAULT_TTL_S, _now: Optional[float] = None) -> Optional[dict]:
    """Return a copy of the newest frame if within ttl_s (non-consuming), else None."""
    if _latest is None:
        return None
    now = _now if _now is not None else time.monotonic()
    if (now - _latest["ts"]) > ttl_s:
        return None
    return dict(_latest)


def clear() -> None:
    """Drop the cached frame + trail (call on a new user turn)."""
    global _latest
    _latest = None
    _recent.clear()


def recent_actions_text() -> str:
    labels = list(_recent)
    return f" (recent: {', '.join(labels)})" if labels else ""


def is_vision_capable(model_id: Optional[str], prefixes=None) -> bool:
    if not model_id:
        return False
    if prefixes is None:
        env = os.environ.get("JARVIS_VISION_MODEL_PREFIXES", "").strip()
        prefixes = tuple(p.strip() for p in env.split(",") if p.strip()) or _VISION_PREFIXES_DEFAULT
    mid = model_id.lower()
    return any(mid.startswith(p.lower()) for p in prefixes)
