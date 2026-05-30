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


def downscale_png(png_b64: str, max_px: int = _MAX_DOWNSCALE_PX) -> Optional[str]:
    """Downscale a base64 PNG so its longest edge <= max_px (aspect preserved);
    return a new base64 PNG. Unchanged if already small. None on any error."""
    if not png_b64:
        return None
    try:
        raw = base64.b64decode(png_b64, validate=True)
    except Exception:
        return None
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(raw))
        img.load()
        w, h = img.size
        longest = max(w, h)
        if longest > max_px and longest > 0:
            scale = max_px / float(longest)
            img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))))
        if img.mode not in ("RGB", "RGBA", "L"):
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        return None


def decide_mode(dispatch_llm=None) -> str:
    """Resolve JARVIS_CU_VISION_MODE. Explicit pixels/text/off win. 'auto' (default)
    best-effort detects the active route's model via dispatch_llm.last_route +
    providers.llm.resolve_route_primary_model; defaults to 'pixels' on any
    uncertainty (the canonical supervisor is Claude / vision-capable)."""
    mode = (os.environ.get("JARVIS_CU_VISION_MODE", "auto").strip().lower() or "auto")
    if mode in ("pixels", "text", "off"):
        return mode
    try:
        route = getattr(dispatch_llm, "last_route", None)
        if route:
            from providers.llm import resolve_route_primary_model
            model = resolve_route_primary_model(route)
            return "pixels" if is_vision_capable(model) else "text"
    except Exception:
        pass
    return "pixels"


def build_injection(*, cap: Optional[dict], mode: str, desc: Optional[str] = None):
    """Return (role, content_list) to add to chat_ctx, or None for no injection.
    pixels → text label + downscaled ImageContent; text → label + description."""
    if not cap or mode == "off":
        return None
    label = cap.get("action_label") or "computer_use"
    trail = recent_actions_text()
    if mode == "pixels":
        b64 = downscale_png(cap.get("png_b64") or "")
        if not b64:
            return None
        from livekit.agents.llm import ImageContent
        return ("user", [f"[screen after: {label}]{trail}",
                         ImageContent(image="data:image/png;base64," + b64,
                                      inference_detail="auto")])
    if mode == "text":
        if not desc:
            return None
        return ("user", [f"[screen after: {label}]{trail} {desc}"])
    return None
