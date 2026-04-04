"""JARVIS Screen Observer — silently watches your screen in real-time.

Capabilities:
- Periodic screenshots (configurable interval)
- Vision model analysis (Anthropic, Ollama llava/llama3.2-vision)
- OCR text extraction as fallback
- Active window detection
- Screen change detection (knows when something changes)
- Context building (understands what you're working on)
- On-demand "what's on my screen" queries
"""

import asyncio
import os
import time
import logging
import threading
import subprocess
from pathlib import Path
from dataclasses import dataclass, field

log = logging.getLogger("jarvis.vision.screen")

SCREENSHOT_DIR = Path(os.environ.get("JARVIS_HOME", Path.home() / ".jarvis")) / "screenshots"


@dataclass
class ScreenContext:
    """What JARVIS knows about the current screen state."""
    active_window: str = ""
    window_class: str = ""
    screen_text: str = ""
    vision_summary: str = ""  # Rich description from vision model
    timestamp: float = 0
    screenshot_path: str = ""
    changed: bool = False


class ScreenObserver:
    """Observes the screen silently and builds context."""

    def __init__(self, interval: float = 5.0, provider_registry=None):
        self.interval = interval
        self._running = False
        self._thread = None
        self._latest: ScreenContext = ScreenContext()
        self._prev_text_hash = ""
        self._history: list[ScreenContext] = []
        self._provider_registry = provider_registry
        self._vision_available = None  # Lazy-detect
        self._vision_interval = 30.0  # Vision analysis every 30s (expensive)
        self._last_vision_time = 0
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

    def set_provider_registry(self, registry):
        """Set provider registry after construction (Brain wires this)."""
        self._provider_registry = registry
        self._vision_available = None  # Re-detect

    @property
    def latest(self) -> ScreenContext:
        return self._latest

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self):
        """Start background screen observation."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._observe_loop, daemon=True, name="jarvis-screen-observer")
        self._thread.start()
        log.info("Screen observer started (interval=%ds)", self.interval)

    def stop(self):
        """Stop observation."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)

    def capture_now(self) -> ScreenContext:
        """Take an immediate screenshot and analyze it."""
        return self._capture()

    def get_screen_text(self) -> str:
        """Get text from current screen (vision or OCR)."""
        ctx = self._capture()
        return ctx.vision_summary or ctx.screen_text

    def get_active_window(self) -> str:
        """Get the currently focused window title."""
        try:
            result = subprocess.run(
                ["xdotool", "getactivewindow", "getwindowname"],
                capture_output=True, text=True, timeout=3,
            )
            return result.stdout.strip()
        except Exception:
            return ""

    def get_active_window_class(self) -> str:
        """Get the window class (app name) of the focused window."""
        try:
            wid = subprocess.run(
                ["xdotool", "getactivewindow"],
                capture_output=True, text=True, timeout=3,
            ).stdout.strip()
            if not wid:
                return ""
            result = subprocess.run(
                ["xprop", "-id", wid, "WM_CLASS"],
                capture_output=True, text=True, timeout=3,
            )
            out = result.stdout.strip()
            if '"' in out:
                parts = out.split('"')
                return parts[3] if len(parts) >= 4 else parts[1]
            return ""
        except Exception:
            return ""

    def what_am_i_looking_at(self) -> str:
        """Human-readable summary of what's on screen."""
        ctx = self.capture_now()
        parts = []
        if ctx.active_window:
            parts.append(f"Active window: {ctx.active_window}")
        if ctx.window_class:
            parts.append(f"Application: {ctx.window_class}")
        if ctx.vision_summary:
            parts.append(f"What I see:\n{ctx.vision_summary}")
        elif ctx.screen_text:
            lines = ctx.screen_text.strip().split("\n")[:10]
            text_preview = "\n".join(lines)
            parts.append(f"Visible text:\n{text_preview}")
        if not parts:
            parts.append("Couldn't read the screen.")
        return "\n".join(parts)

    def analyze_screen(self, prompt: str = "") -> str:
        """On-demand deep analysis using vision model."""
        ctx = self._capture(force_vision=True, vision_prompt=prompt)
        return ctx.vision_summary or ctx.screen_text or "No screen data available."

    def get_context_for_llm(self) -> str:
        """Get screen context formatted for injection into LLM prompt."""
        ctx = self._latest
        if not ctx.active_window and not ctx.screen_text and not ctx.vision_summary:
            return ""
        parts = []
        if ctx.active_window:
            parts.append(f"User is looking at: {ctx.active_window} ({ctx.window_class})")
        if ctx.vision_summary:
            parts.append(f"Screen context: {ctx.vision_summary[:500]}")
        elif ctx.screen_text:
            text = ctx.screen_text[:500]
            parts.append(f"Screen text: {text}")
        return "\n".join(parts)

    def get_screenshot_base64(self) -> str:
        """Get latest screenshot as base64 JPEG for vision models."""
        ctx = self._latest
        path = ctx.screenshot_path
        if not path or not os.path.exists(path):
            ctx = self.capture_now()
            path = ctx.screenshot_path
        if not path or not os.path.exists(path):
            return ""
        try:
            import base64
            from PIL import Image
            from io import BytesIO
            img = Image.open(path)
            img.thumbnail((1920, 1080))
            buf = BytesIO()
            img.save(buf, format="JPEG", quality=80)
            return base64.b64encode(buf.getvalue()).decode()
        except Exception as e:
            log.debug("Screenshot base64 encoding failed: %s", e)
            return ""

    def history(self, limit: int = 10) -> list[dict]:
        """Recent screen observations."""
        return [
            {"window": h.active_window, "app": h.window_class,
             "time": h.timestamp, "changed": h.changed,
             "has_vision": bool(h.vision_summary)}
            for h in self._history[-limit:]
        ]

    # ── Vision Model Integration ──

    def _check_vision_available(self) -> bool:
        """Check if any vision-capable provider is available."""
        if self._vision_available is not None:
            return self._vision_available

        # Check Ollama for vision models
        try:
            result = subprocess.run(
                ["ollama", "list"], capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.lower().split("\n"):
                if any(v in line for v in ["moondream", "llava", "vision", "llama3.2", "minicpm-v"]):
                    self._vision_available = True
                    log.info("Vision model available via Ollama")
                    return True
        except Exception:
            pass

        # Check if provider registry has an Anthropic provider (supports vision)
        if self._provider_registry:
            for p in self._provider_registry.get_active_providers():
                if p.type == "anthropic":
                    self._vision_available = True
                    log.info("Vision available via Anthropic provider")
                    return True

        self._vision_available = False
        return False

    async def _query_vision_async(self, image_b64: str, prompt: str = "") -> str:
        """Query a vision model with a screenshot."""
        if not prompt:
            prompt = (
                "Describe what's on this screen concisely. Focus on: "
                "what application is open, what the user is working on, "
                "any important text or UI elements visible. "
                "Keep it under 200 words."
            )

        # Try Ollama vision models first (local, fast, dedicated vision model)
        try:
            result = await self._query_ollama_vision(image_b64, prompt)
            if result and len(result) > 20:
                log.debug("Vision analysis via Ollama")
                return result
        except Exception as e:
            log.debug("Ollama vision failed: %s", e)

        # Fallback to provider registry (Anthropic vision, etc.)
        if self._provider_registry:
            try:
                result, provider = await self._provider_registry.query_vision(
                    image_b64, prompt
                )
                if result and len(result) > 20:
                    log.debug("Vision analysis via %s", provider)
                    return result
            except Exception as e:
                log.debug("Provider vision failed: %s", e)

        return ""

    async def _query_ollama_vision(self, image_b64: str, prompt: str) -> str:
        """Query Ollama vision model (llava, llama3.2-vision, etc.)."""
        import aiohttp

        ollama_url = os.environ.get("OLLAMA_URL", "http://localhost:11434")

        # Find a vision model
        # Prefer moondream (small, GPU-friendly), then other vision models
        vision_model = None
        vision_priority = ["moondream", "minicpm-v", "llava", "llama3.2", "vision"]
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{ollama_url}/api/tags", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    data = await resp.json()
                    candidates = []
                    for model in data.get("models", []):
                        name = model.get("name", "").lower()
                        for i, v in enumerate(vision_priority):
                            if v in name:
                                candidates.append((i, model["name"]))
                                break
                    if candidates:
                        candidates.sort(key=lambda x: x[0])
                        vision_model = candidates[0][1]
        except Exception:
            pass

        if not vision_model:
            return ""

        # Query Ollama with image
        payload = {
            "model": vision_model,
            "prompt": prompt,
            "images": [image_b64],
            "stream": False,
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{ollama_url}/api/generate",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    data = await resp.json()
                    return data.get("response", "")
        except Exception as e:
            log.debug("Ollama vision query failed: %s", e)
            return ""

    def _run_vision_sync(self, image_b64: str, prompt: str = "") -> str:
        """Run vision query synchronously (for background thread)."""
        try:
            loop = asyncio.new_event_loop()
            result = loop.run_until_complete(self._query_vision_async(image_b64, prompt))
            loop.close()
            return result
        except Exception as e:
            log.debug("Vision sync failed: %s", e)
            return ""

    # ── Internal ──

    def _observe_loop(self):
        """Background loop that periodically captures the screen."""
        while self._running:
            try:
                self._capture()
            except Exception as e:
                log.debug("Screen capture error: %s", e)
            time.sleep(self.interval)

    def _capture(self, force_vision: bool = False, vision_prompt: str = "") -> ScreenContext:
        """Take a screenshot, extract text, detect window."""
        ctx = ScreenContext(timestamp=time.time())

        # Get active window
        ctx.active_window = self.get_active_window()
        ctx.window_class = self.get_active_window_class()

        # Screenshot — prefer mss (fast, silent), fall back to scrot
        path = str(SCREENSHOT_DIR / "latest.png")
        captured = False
        try:
            import mss
            with mss.mss() as sct:
                sct.shot(output=path)
            captured = True
        except Exception as e:
            log.debug("mss screenshot failed: %s", e)
        if not captured:
            try:
                subprocess.run(
                    ["scrot", "-o", path],
                    capture_output=True, timeout=5,
                )
                captured = True
            except Exception as e:
                log.debug("scrot screenshot failed: %s", e)
        if captured:
            ctx.screenshot_path = path

        # Vision analysis (periodic or on-demand)
        now = time.time()
        use_vision = (force_vision or
                      (now - self._last_vision_time > self._vision_interval and ctx.changed is not False))

        if use_vision and ctx.screenshot_path and self._check_vision_available():
            b64 = self.get_screenshot_base64()
            if b64:
                ctx.vision_summary = self._run_vision_sync(b64, vision_prompt)
                if ctx.vision_summary:
                    self._last_vision_time = now
                    log.debug("Vision analysis completed (%d chars)", len(ctx.vision_summary))

        # OCR fallback (always run for text extraction)
        if ctx.screenshot_path and os.path.exists(ctx.screenshot_path):
            try:
                import pytesseract
                from PIL import Image
                img = Image.open(ctx.screenshot_path)
                img.thumbnail((1920, 1080))
                ctx.screen_text = pytesseract.image_to_string(img, timeout=10)
            except Exception as e:
                log.debug("OCR failed: %s", e)

        # Change detection
        text_hash = hash((ctx.screen_text[:200] if ctx.screen_text else "") + ctx.active_window)
        ctx.changed = text_hash != self._prev_text_hash
        self._prev_text_hash = text_hash

        self._latest = ctx
        self._history.append(ctx)
        if len(self._history) > 100:
            self._history = self._history[-50:]

        return ctx

    def _take_screenshot_silent(self) -> str:
        """Take screenshot without any visible indicator."""
        path = str(SCREENSHOT_DIR / f"cap_{int(time.time())}.png")
        try:
            import mss
            with mss.mss() as sct:
                sct.shot(output=path)
            return path
        except ImportError:
            pass
        try:
            subprocess.run(["scrot", "-o", path], capture_output=True, timeout=5)
            return path
        except Exception:
            return ""
