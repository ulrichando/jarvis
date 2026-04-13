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
    ui_tree: str = ""         # AT-SPI accessibility tree summary
    timestamp: float = 0
    screenshot_path: str = ""
    changed: bool = False


def _read_atspi_tree(max_depth: int = 3, max_elements: int = 30) -> str:
    """Read the AT-SPI accessibility tree of the focused application.

    Returns a compact summary of UI elements: buttons, text fields,
    labels, menus — what's on screen without needing a screenshot.
    Fast (~50ms), local, no API cost.

    Falls back to window list via wmctrl if AT-SPI doesn't find the focused app.
    """
    result = ""

    # Try AT-SPI first
    try:
        import gi
        gi.require_version('Atspi', '2.0')
        from gi.repository import Atspi

        desktop = Atspi.get_desktop(0)
        if desktop:
            # Find the focused application
            focused_app = None
            for i in range(desktop.get_child_count()):
                app = desktop.get_child_at_index(i)
                if not app:
                    continue
                try:
                    for j in range(min(app.get_child_count(), 10)):
                        win = app.get_child_at_index(j)
                        if win and win.get_state_set().contains(Atspi.StateType.ACTIVE):
                            focused_app = app
                            break
                except Exception:
                    continue
                if focused_app:
                    break

            if focused_app:
                elements = []
                _count = [0]

                def _walk(node, depth=0):
                    if _count[0] >= max_elements or depth > max_depth:
                        return
                    if not node:
                        return
                    try:
                        role = node.get_role_name() or ""
                        name = node.get_name() or ""
                        if not name and role in ("filler", "panel", "section", "redundant object"):
                            for i in range(min(node.get_child_count(), 8)):
                                _walk(node.get_child_at_index(i), depth + 1)
                            return
                        indent = "  " * depth
                        if name:
                            elements.append(f"{indent}[{role}] {name}")
                            _count[0] += 1
                        elif role in ("button", "text", "entry", "menu item", "link",
                                      "tab", "check box", "radio button", "combo box"):
                            elements.append(f"{indent}[{role}]")
                            _count[0] += 1
                        for i in range(min(node.get_child_count(), 8)):
                            _walk(node.get_child_at_index(i), depth + 1)
                    except Exception:
                        pass

                app_name = focused_app.get_name() or "Unknown"
                elements.append(f"App: {app_name}")
                for i in range(min(focused_app.get_child_count(), 5)):
                    _walk(focused_app.get_child_at_index(i), 1)

                if len(elements) > 1:  # More than just "App: name"
                    result = "\n".join(elements)
    except Exception as e:
        log.debug("AT-SPI read failed: %s", e)

    # Fallback: list all open windows via wmctrl
    if not result or result.count("\n") < 2:
        try:
            wmctrl = subprocess.run(
                ["wmctrl", "-l", "-p"],
                capture_output=True, text=True, timeout=3,
            )
            if wmctrl.returncode == 0:
                lines = []
                for line in wmctrl.stdout.strip().split("\n"):
                    parts = line.split(None, 4)
                    if len(parts) >= 5:
                        title = parts[4]
                        if title and title not in ("Desktop", "xfce4-panel"):
                            lines.append(f"  Window: {title}")
                if lines:
                    result = "Open windows:\n" + "\n".join(lines[:10])
        except Exception:
            pass

    return result


def _track_window_focus() -> tuple[str, str]:
    """Get current focused window title and class. Fast (~5ms)."""
    title, wclass = "", ""
    try:
        wid = subprocess.run(
            ["xdotool", "getactivewindow"],
            capture_output=True, text=True, timeout=2,
        ).stdout.strip()
        if not wid:
            return "", ""
        title = subprocess.run(
            ["xdotool", "getwindowname", wid],
            capture_output=True, text=True, timeout=2,
        ).stdout.strip()
        xprop = subprocess.run(
            ["xprop", "-id", wid, "WM_CLASS"],
            capture_output=True, text=True, timeout=2,
        )
        out = xprop.stdout.strip()
        if '"' in out:
            parts = out.split('"')
            wclass = parts[3] if len(parts) >= 4 else parts[1]
    except Exception:
        pass
    return title, wclass


class ScreenObserver:
    """Observes the screen using 3 combined methods:

    1. Window focus tracking (instant, free) — always knows what app is active
    2. AT-SPI accessibility tree (fast, local) — structured UI elements
    3. Periodic screenshots + vision (rich, expensive) — only when needed
    """

    def __init__(self, interval: float = 5.0, provider_registry=None):
        self.interval = interval
        self._running = False
        self._thread = None
        self._latest: ScreenContext = ScreenContext()
        self._prev_text_hash = ""
        self._prev_window = ""
        self._history: list[ScreenContext] = []
        self._provider_registry = provider_registry
        self._vision_available = None  # Lazy-detect
        self._vision_interval = 30.0  # Vision analysis every 30s (expensive)
        self._atspi_interval = 3.0    # AT-SPI every 3s (cheap)
        self._last_vision_time = 0
        self._last_atspi_time = 0
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
        """Get screen context formatted for injection into LLM prompt.

        Combines all 3 awareness methods:
        1. Window focus (what app)
        2. AT-SPI tree (what UI elements are visible)
        3. Vision/OCR (what the screen looks like)
        """
        ctx = self._latest
        if not ctx.active_window and not ctx.screen_text and not ctx.vision_summary and not ctx.ui_tree:
            return ""
        parts = []
        if ctx.active_window:
            parts.append(f"Active window: {ctx.active_window} ({ctx.window_class})")
        if ctx.ui_tree:
            # Compact AT-SPI summary — most useful for understanding UI state
            parts.append(f"UI elements:\n{ctx.ui_tree[:600]}")
        if ctx.vision_summary:
            parts.append(f"Visual: {ctx.vision_summary[:400]}")
        elif ctx.screen_text:
            text = ctx.screen_text[:400]
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
        """Background loop — fast window+AT-SPI tracking, slow screenshots."""
        while self._running:
            try:
                now = time.time()

                # Always track window focus (instant, ~5ms)
                title, wclass = _track_window_focus()
                window_changed = title != self._prev_window
                self._prev_window = title

                # AT-SPI on interval or window change (fast, ~50ms)
                ui_tree = ""
                if window_changed or now - self._last_atspi_time > self._atspi_interval:
                    ui_tree = _read_atspi_tree()
                    self._last_atspi_time = now

                # Update latest context without full screenshot
                self._latest.active_window = title
                self._latest.window_class = wclass
                self._latest.timestamp = now
                if ui_tree:
                    self._latest.ui_tree = ui_tree
                self._latest.changed = window_changed

                # Full screenshot capture on interval (expensive)
                if now - (self._latest.timestamp or 0) > self.interval or window_changed:
                    self._capture()

            except Exception as e:
                log.debug("Screen observe error: %s", e)
            time.sleep(1)  # Fast tick for window tracking

    def _capture(self, force_vision: bool = False, vision_prompt: str = "") -> ScreenContext:
        """Take a screenshot, extract text, detect window, read AT-SPI tree."""
        ctx = ScreenContext(timestamp=time.time())

        # Get active window (use fast tracker)
        ctx.active_window, ctx.window_class = _track_window_focus()

        # AT-SPI accessibility tree
        try:
            ctx.ui_tree = _read_atspi_tree()
        except Exception:
            pass

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

        # OCR — only on explicit on-demand requests (force_vision=True)
        # Never run automatically; pytesseract can block for up to 10s per frame
        if force_vision and ctx.screenshot_path and os.path.exists(ctx.screenshot_path):
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
