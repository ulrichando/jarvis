"""Vision tap sidecar — periodic screen capture → Kimi vision LLM →
blackboard.screen.* facts.

Architecture (spec §5.2):
  Active-window watcher (xdotool) ──► change-event detector
                                          │
                                          ▼
                              Throttled snapshot trigger
                                          │
                                          ▼
                              scrot ─► PNG file ─► base64
                                          │
                                          ▼
                              Kimi vision (moonshot-v1-32k-vision-preview)
                                          │
                                          ▼
                              ScreenFact ─► blackboard.write_screen_fact

This module exposes a `main()` for the systemd unit and helper
functions used by tests. Each layer is independently testable.
"""
from __future__ import annotations

import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger("vision_tap")


def _screenshot_path() -> Path:
    """The path scrot writes to. Overridable for tests."""
    return Path(tempfile.gettempdir()) / "jarvis-vision.png"


def capture_screenshot() -> Optional[Path]:
    """Capture the full screen via scrot. Returns the PNG path on
    success, None on failure (Wayland-restricted, scrot not running,
    file write error)."""
    out_path = _screenshot_path()
    try:
        result = subprocess.run(
            ["scrot", "-o", str(out_path)],
            capture_output=True, timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        logger.warning("[vision-tap] scrot failed: %s", e)
        return None
    if result.returncode != 0:
        logger.warning(
            "[vision-tap] scrot returned %d: %s",
            result.returncode, result.stderr,
        )
        return None
    return out_path


def get_active_app() -> Optional[str]:
    """Get the active window's WM class via xdotool. Used as a
    cheap screen-change signal — when the active app changes, we
    refresh the screen fact."""
    try:
        result = subprocess.run(
            ["xdotool", "getactivewindow", "getwindowclassname"],
            capture_output=True, text=True, timeout=2,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    if result.returncode != 0:
        return None
    name = (result.stdout or "").strip()
    return name or None


import base64
import json
import os
import time

import requests

from blackboard.schema import ScreenFact


_VISION_SYSTEM_PROMPT = (
    "Respond ONLY with a JSON object matching this schema:\n"
    "  {\"active_app\": str | null,\n"
    "   \"foreground_url\": str | null,\n"
    "   \"tab_count\": int | null,\n"
    "   \"dom_summary\": str | null,\n"
    "   \"uncertain\": bool,\n"
    "   \"reason\": str | null}\n\n"
    "English only. Be concise: name the active application, count "
    "visible tabs, identify the foreground content. Do NOT describe "
    "pixel-level details. If you cannot tell, return uncertain=true "
    "with a one-sentence reason."
)


def describe_screen(png_bytes: bytes) -> Optional[ScreenFact]:
    """Send a PNG screenshot to Kimi vision and parse the response
    into a ScreenFact. Returns None on any failure (HTTP error,
    invalid JSON, schema mismatch).

    The Moonshot API requires base64-encoded image data — external
    URLs are rejected (verified live 2026-05-04)."""
    api_key = os.environ.get("KIMI_API_KEY", "")
    if not api_key:
        logger.warning("[vision-tap] KIMI_API_KEY not set; skipping vision call")
        return None
    b64 = base64.b64encode(png_bytes).decode("ascii")
    payload = {
        "model": "moonshot-v1-32k-vision-preview",
        "messages": [
            {"role": "system", "content": _VISION_SYSTEM_PROMPT},
            {"role": "user", "content": [
                {"type": "text", "text": "describe this screen"},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ]},
        ],
        "max_tokens": 300,
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
    }
    try:
        resp = requests.post(
            "https://api.moonshot.ai/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}",
                     "Content-Type": "application/json"},
            json=payload, timeout=30,
        )
    except Exception as e:
        logger.warning("[vision-tap] vision request failed: %s: %s",
                       type(e).__name__, e)
        return None
    if resp.status_code != 200:
        logger.warning("[vision-tap] vision HTTP %d: %s",
                       resp.status_code, getattr(resp, "text", "")[:200])
        return None

    try:
        body = resp.json()
        content = body.get("choices", [{}])[0].get("message", {}).get("content", "")
    except Exception as e:
        logger.warning("[vision-tap] response parse failed: %s", e)
        return None

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        logger.warning("[vision-tap] non-JSON response: %r", content[:200])
        return None

    try:
        return ScreenFact(
            active_app=parsed.get("active_app"),
            foreground_url=parsed.get("foreground_url"),
            tab_count=parsed.get("tab_count"),
            dom_summary=parsed.get("dom_summary"),
            uncertain=parsed.get("uncertain", False),
            reason=parsed.get("reason"),
            captured_at=time.time(),
        )
    except Exception as e:
        logger.warning("[vision-tap] schema validation failed: %s", e)
        return None


class VisionTapThrottle:
    """Decides when to capture a screenshot.

    Three gates:
      - paused_apps: never capture if active_app is in this set
        (privacy gate — banking, password manager, etc.)
      - min_interval: don't capture more than once per N seconds
        (cost gate — vision calls cost ~$0.02 each)
      - max_interval: ALWAYS capture if N seconds have elapsed
        even if the active app hasn't changed (freshness gate)
      - active-app change: capture on app switch (after min_interval)
    """

    def __init__(
        self,
        *,
        min_interval: float = 1.0,
        max_interval: float = 30.0,
        paused_apps: Optional[set[str]] = None,
    ) -> None:
        self.min_interval = min_interval
        self.max_interval = max_interval
        self.paused_apps = paused_apps or set()
        self._last_captured_at: float = 0.0
        self._last_active_app: Optional[str] = None

    def should_capture(self, *, active_app: Optional[str]) -> bool:
        if active_app and active_app.lower() in self.paused_apps:
            return False
        elapsed = time.time() - self._last_captured_at
        if elapsed >= self.max_interval:
            return True
        if elapsed < self.min_interval:
            return False
        # min_interval ≤ elapsed < max_interval — capture only on app change.
        return active_app != self._last_active_app

    def mark_captured(self, *, active_app: Optional[str] = None) -> None:
        self._last_captured_at = time.time()
        if active_app is not None:
            self._last_active_app = active_app


def _load_paused_apps() -> set[str]:
    """Read ~/.jarvis/vision-paused-apps.txt — one app name per line.
    Missing file → empty set."""
    path = Path.home() / ".jarvis" / "vision-paused-apps.txt"
    if not path.exists():
        return set()
    return {
        line.strip().lower()
        for line in path.read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    }


def main() -> None:
    """Sidecar entry point. Loop: probe active app → maybe capture →
    maybe send to vision LLM → write to blackboard.

    Exits cleanly on SIGTERM (systemd stop). All errors are logged and
    swallowed — vision_tap is non-essential and must never bring down
    the voice agent.
    """
    import signal

    logging.basicConfig(
        level=os.environ.get("VISION_TAP_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)-7s vision-tap %(message)s",
    )
    logger.info("[vision-tap] starting")

    from blackboard.client import BlackboardClient

    bb = BlackboardClient()
    throttle = VisionTapThrottle(
        min_interval=float(os.environ.get("VISION_TAP_MIN_INTERVAL", "1.0")),
        max_interval=float(os.environ.get("VISION_TAP_MAX_INTERVAL", "30.0")),
        paused_apps=_load_paused_apps(),
    )

    stop = False

    def _on_sigterm(signum, frame):
        nonlocal stop
        logger.info("[vision-tap] SIGTERM received; stopping")
        stop = True

    signal.signal(signal.SIGTERM, _on_sigterm)
    signal.signal(signal.SIGINT, _on_sigterm)

    while not stop:
        try:
            active_app = get_active_app()
            if not throttle.should_capture(active_app=active_app):
                time.sleep(0.5)
                continue
            png_path = capture_screenshot()
            if png_path is None:
                throttle.mark_captured(active_app=active_app)  # don't tight-loop on scrot failure
                time.sleep(2.0)
                continue
            png_bytes = png_path.read_bytes()
            fact = describe_screen(png_bytes)
            if fact is not None:
                bb.write_screen_fact(fact)
                logger.info("[vision-tap] fact written: app=%r tabs=%r",
                            fact.active_app, fact.tab_count)
            else:
                logger.info("[vision-tap] no fact (vision call failed or invalid)")
            throttle.mark_captured(active_app=active_app)
        except Exception as e:
            logger.exception("[vision-tap] loop error: %s", e)
            time.sleep(2.0)

    logger.info("[vision-tap] stopped cleanly")


if __name__ == "__main__":
    main()
