"""Linux X11 automation backend for the ``computer_use`` tool.

Ported from the upstream computer-use toolset, which shipped a macOS-only
``cua-driver`` (MCP/SkyLight) backend. JARVIS runs on Linux/X11, so this
module re-implements the same abstract :class:`ComputerUseBackend` surface
using locally-present command-line tools:

  * **Input** (click / move / drag / scroll / type / key): ``xdotool`` via
    subprocess. ``pyautogui`` / ``python-xlib`` / ``pynput`` are NOT installed
    on this host, so subprocess ``xdotool`` is the deliberate choice.
  * **Screenshots**: ``mss`` (fast, pure-Python) when importable, else
    ImageMagick ``import`` as a fallback. Both are present.
  * **Window introspection** (list windows / focus app): ``wmctrl`` + the
    ``xdotool`` window stack.

Unlike the macOS backend, there is NO accessibility (AX) tree on stock X11.
``capture(mode='som')`` renders numbered red/orange bounding-box overlays
on each window from the ``wmctrl`` window list and returns the annotated
screenshot. Element-index targeting (``click element=3`` / ``scroll
element=2`` / ``drag from_element=1 to_element=2``) resolves the 1-based
index to the center of the element's bounding box, which is more reliable
than guessing pixel coordinates. ``capture(mode='vision')`` returns a clean
screenshot. ``capture(mode='ax')`` returns the window list only (no image).

All public methods are synchronous (the abstract contract). Every shell-out is
wrapped so a missing/locked display surfaces as an ``ActionResult(ok=False)``
rather than an exception that would crash the turn.
"""
from __future__ import annotations

import base64
import io
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Abstract surface (ported verbatim in shape from the upstream backend; the
# macOS-specific dataclass fields like ``window_id`` / ``pid`` are kept so the
# return contract matches, even though the X11 backend leaves some unused).
# ---------------------------------------------------------------------------


@dataclass
class UIElement:
    """One interactable element. On X11 (no AX tree) these come from the
    window list, not per-control accessibility, so most carry only a window
    title + geometry."""

    index: int                       # 1-based index
    role: str = ""                   # window/control role (best-effort)
    label: str = ""                  # window title / description
    bounds: Tuple[int, int, int, int] = (0, 0, 0, 0)  # x, y, w, h (px)
    app: str = ""                    # owning app / WM_CLASS
    pid: int = 0                     # owning process PID
    window_id: int = 0               # X11 window id
    attributes: Dict[str, Any] = field(default_factory=dict)

    def center(self) -> Tuple[int, int]:
        x, y, w, h = self.bounds
        return x + w // 2, y + h // 2


@dataclass
class CaptureResult:
    """Result of a screen capture call."""

    mode: str
    width: int
    height: int
    png_b64: Optional[str] = None
    elements: List[UIElement] = field(default_factory=list)
    app: str = ""
    window_title: str = ""
    png_bytes_len: int = 0


@dataclass
class ActionResult:
    """Result of any action (click / type / scroll / drag / key / wait)."""

    ok: bool
    action: str
    message: str = ""
    capture: Optional[CaptureResult] = None
    meta: Dict[str, Any] = field(default_factory=dict)


class ComputerUseBackend(ABC):
    """Lifecycle: ``start()`` before first use, ``stop()`` at shutdown."""

    @abstractmethod
    def start(self) -> None: ...

    @abstractmethod
    def stop(self) -> None: ...

    @abstractmethod
    def is_available(self) -> bool: ...

    @abstractmethod
    def capture(self, mode: str = "som", app: Optional[str] = None,
                region: Optional[List[int]] = None) -> CaptureResult: ...

    @abstractmethod
    def click(
        self,
        *,
        element: Optional[int] = None,
        x: Optional[int] = None,
        y: Optional[int] = None,
        button: str = "left",
        click_count: int = 1,
        modifiers: Optional[List[str]] = None,
    ) -> ActionResult: ...

    @abstractmethod
    def drag(
        self,
        *,
        from_element: Optional[int] = None,
        to_element: Optional[int] = None,
        from_xy: Optional[Tuple[int, int]] = None,
        to_xy: Optional[Tuple[int, int]] = None,
        button: str = "left",
        modifiers: Optional[List[str]] = None,
    ) -> ActionResult: ...

    @abstractmethod
    def scroll(
        self,
        *,
        direction: str,
        amount: int = 3,
        element: Optional[int] = None,
        x: Optional[int] = None,
        y: Optional[int] = None,
        modifiers: Optional[List[str]] = None,
    ) -> ActionResult: ...

    @abstractmethod
    def type_text(self, text: str) -> ActionResult: ...

    @abstractmethod
    def key(self, keys: str) -> ActionResult: ...

    @abstractmethod
    def list_apps(self) -> List[Dict[str, Any]]: ...

    @abstractmethod
    def list_available_apps(self) -> List[Dict[str, Any]]: ...

    @abstractmethod
    def launch_app(self, command: str) -> ActionResult: ...

    @abstractmethod
    def move_cursor(
        self,
        *,
        element: Optional[int] = None,
        x: Optional[int] = None,
        y: Optional[int] = None,
    ) -> ActionResult: ...

    @abstractmethod
    def get_cursor_position(self) -> ActionResult: ...

    @abstractmethod
    def mouse_down(
        self,
        *,
        element: Optional[int] = None,
        x: Optional[int] = None,
        y: Optional[int] = None,
        button: str = "left",
        modifiers: Optional[List[str]] = None,
    ) -> ActionResult: ...

    @abstractmethod
    def mouse_up(
        self,
        *,
        element: Optional[int] = None,
        x: Optional[int] = None,
        y: Optional[int] = None,
        button: str = "left",
        modifiers: Optional[List[str]] = None,
    ) -> ActionResult: ...

    @abstractmethod
    def hold_key(self, keys: str, seconds: float = 1.0) -> ActionResult: ...

    @abstractmethod
    def key_down(self, keys: str) -> ActionResult: ...

    @abstractmethod
    def key_up(self, keys: str) -> ActionResult: ...

    @abstractmethod
    def focus_app(self, app: str, raise_window: bool = False) -> ActionResult: ...

    def wait(self, seconds: float) -> ActionResult:
        time.sleep(max(0.0, min(seconds, 30.0)))
        return ActionResult(ok=True, action="wait", message=f"waited {seconds:.2f}s")


# ---------------------------------------------------------------------------
# Availability helpers (used by the tool's check_fn)
# ---------------------------------------------------------------------------

_XDOTOOL = os.environ.get("JARVIS_COMPUTER_USE_XDOTOOL", "xdotool")


def _has_display() -> bool:
    """True when an X11 display is reachable for this process."""
    return bool(os.environ.get("DISPLAY", "").strip())


def xdotool_available() -> bool:
    """True if the ``xdotool`` binary resolves on ``$PATH``."""
    return bool(shutil.which(_XDOTOOL))


def x11_backend_available() -> bool:
    """True iff the X11 computer-use backend can drive input right now.

    Conditions: a non-Windows POSIX host (Linux), ``$DISPLAY`` set, and
    ``xdotool`` installed. This is the gate the registry ``check_fn`` uses so
    the tool registers inert in headless / CI environments.
    """
    if sys.platform == "win32":  # pragma: no cover - not our deployment target
        return False
    return _has_display() and xdotool_available()


def _screenshot_command_available() -> bool:
    """True if at least one screenshot mechanism is usable (mss or import)."""
    try:
        import mss  # noqa: F401

        return True
    except Exception:
        return bool(shutil.which("import") or shutil.which("scrot"))


# ---------------------------------------------------------------------------
# Key-name translation: tool combo syntax -> xdotool keysyms
# ---------------------------------------------------------------------------

# Modifier aliases the LLM might emit -> xdotool modifier keysym.
_MODIFIER_KEYSYMS = {
    "ctrl": "ctrl",
    "control": "ctrl",
    "cmd": "super",      # no Command key on Linux; map to Super
    "command": "super",
    "super": "super",
    "win": "super",
    "meta": "super",
    "alt": "alt",
    "option": "alt",
    "shift": "shift",
    "fn": "",            # no portable Fn keysym; drop
}

# Bare-key aliases -> xdotool keysym names.
_KEY_KEYSYMS = {
    "return": "Return",
    "enter": "Return",
    "esc": "Escape",
    "escape": "Escape",
    "tab": "Tab",
    "space": "space",
    "backspace": "BackSpace",
    "delete": "Delete",
    "del": "Delete",
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
    "home": "Home",
    "end": "End",
    "pageup": "Prior",
    "pagedown": "Next",
}


def _xdotool_keysym(part: str) -> str:
    """Translate one combo token into an xdotool keysym."""
    p = part.strip().lower()
    if not p:
        return ""
    if p in _MODIFIER_KEYSYMS:
        return _MODIFIER_KEYSYMS[p]
    if p in _KEY_KEYSYMS:
        return _KEY_KEYSYMS[p]
    if len(p) == 1:
        # single char: pass through (xdotool handles letters/digits/punct)
        return part.strip()
    if re.fullmatch(r"f\d{1,2}", p):  # function keys F1..F12
        return p.upper()
    # Unknown multi-char token: title-case as a best-effort keysym (e.g. "Insert").
    return part.strip().capitalize()


def parse_key_combo_to_xdotool(keys: str) -> str:
    """Convert e.g. ``'ctrl+s'`` -> xdotool ``'ctrl+s'`` keysym string.

    Returns a ``'+'``-joined keysym sequence suitable for ``xdotool key``.
    """
    parts = [p for p in re.split(r"\s*\+\s*", keys) if p.strip()]
    syms = [s for s in (_xdotool_keysym(p) for p in parts) if s]
    return "+".join(syms)


# Map tool button names -> xdotool button numbers.
_BUTTON_NUM = {"left": "1", "middle": "2", "right": "3"}
# Scroll directions -> xdotool button numbers (4=up,5=down,6=left,7=right).
_SCROLL_BUTTON = {"up": "4", "down": "5", "left": "6", "right": "7"}


# ---------------------------------------------------------------------------
# The X11 backend
# ---------------------------------------------------------------------------


class X11ComputerUseBackend(ComputerUseBackend):
    """Linux/X11 backend driving input through ``xdotool`` and capturing the
    screen via ``mss`` (preferred) or ImageMagick ``import``."""

    def __init__(self) -> None:
        self._started = False
        # Default subprocess timeout for any xdotool/wmctrl call.
        self._timeout = float(os.environ.get("JARVIS_COMPUTER_USE_TIMEOUT", "10"))
        # Cache of the last captured element list (used for element-index
        # resolution in click / scroll / drag). Populated by capture() when
        # mode is 'som' or 'ax'. Cleared on every new capture.
        self._last_elements: List[UIElement] = []

    # ── Lifecycle ──────────────────────────────────────────────────
    def start(self) -> None:
        self._started = True

    def stop(self) -> None:
        self._started = False

    def is_available(self) -> bool:
        return x11_backend_available()

    # ── Subprocess helper ──────────────────────────────────────────
    def _run(self, argv: List[str]) -> Tuple[int, str, str]:
        """Run *argv*, return (returncode, stdout, stderr). Never raises."""
        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=self._timeout,
                env=os.environ.copy(),
            )
            return proc.returncode, proc.stdout or "", proc.stderr or ""
        except FileNotFoundError as e:
            return 127, "", f"binary not found: {e}"
        except subprocess.TimeoutExpired:
            return 124, "", f"timed out after {self._timeout}s"
        except Exception as e:  # noqa: BLE001 — surface as a failed action, never crash
            return -1, "", str(e)

    def _xdo(self, *args: str) -> Tuple[int, str, str]:
        return self._run([_XDOTOOL, *args])

    # ── Modifier helpers (xdotool keydown/keyup around an action) ───
    def _press_modifiers(self, modifiers: Optional[List[str]]) -> List[str]:
        """Return the list of xdotool keysyms for held modifiers, pressing
        them down. Caller must release with :meth:`_release_modifiers`."""
        syms: List[str] = []
        for m in modifiers or []:
            sym = _MODIFIER_KEYSYMS.get(m.strip().lower())
            if sym:
                syms.append(sym)
        for sym in syms:
            self._xdo("keydown", sym)
        return syms

    def _release_modifiers(self, syms: List[str]) -> None:
        for sym in reversed(syms):
            self._xdo("keyup", sym)

    # ── Capture ────────────────────────────────────────────────────
    def capture(self, mode: str = "som", app: Optional[str] = None,
                region: Optional[List[int]] = None) -> CaptureResult:
        """Capture the screen as a PNG (base64) plus a best-effort window list.

        SOM mode (default): renders numbered red rectangles (Set of Markers)
        for each window element directly onto the screenshot before returning it.
        The returned ``elements`` list maps 1-based indices to window bounds, so
        the LLM can target elements by index (``click element=2``) instead of
        guessing pixel coordinates.

        AX mode: same screenshot + window list, no overlays.
        Vision mode: screenshot only, no overlay, no window list.

        Region: optional [x1, y1, x2, y2] crops the screenshot to that sub-region
        at 1:1 pixel mapping (no downscale) — the "zoom" equivalent for reading
        small text or inspecting UI details.
        """
        png_b64: Optional[str] = None
        width = height = 0

        if mode != "ax":
            png_b64, width, height = self._screenshot_b64()
            # Region crop: extract a sub-rectangle from the full screenshot at
            # 1:1 pixel mapping. Done BEFORE SOM overlay rendering so overlays
            # are drawn relative to the cropped region.
            if region is not None and png_b64 and len(region) == 4:
                png_b64, width, height = self._crop_png(
                    png_b64, width, height,
                    int(region[0]), int(region[1]),
                    int(region[2]), int(region[3]),
                )

        elements: List[UIElement] = []
        app_name = ""
        window_title = ""
        if mode in {"som", "ax"}:
            elements = self._enumerate_windows(app)
            if app and elements:
                app_name = elements[0].app
                window_title = elements[0].label

        # Cache elements for element-index resolution by click/scroll/drag.
        # Cleared on every new capture so stale indices can't survive.
        self._last_elements = elements

        # SOM mode: render numbered overlays onto the screenshot.
        if mode == "som" and png_b64 and elements:
            png_b64, width, height = self._render_som_overlays(
                png_b64, width, height, elements
            )

        png_bytes_len = 0
        if png_b64:
            try:
                png_bytes_len = len(base64.b64decode(png_b64, validate=False))
            except Exception:
                png_bytes_len = len(png_b64) * 3 // 4

        return CaptureResult(
            mode=mode,
            width=width,
            height=height,
            png_b64=png_b64,
            elements=elements,
            app=app_name,
            window_title=window_title,
            png_bytes_len=png_bytes_len,
        )

    def _render_som_overlays(
        self,
        png_b64: str,
        width: int,
        height: int,
        elements: List[UIElement],
    ) -> Tuple[Optional[str], int, int]:
        """Render numbered red rectangles on a screenshot for each window element.

        Uses Pillow (PIL) to draw numbered overlays. Best-effort: returns the
        original screenshot unchanged on any error (PIL missing, corrupt data,
        coordinate out of bounds) so a rendering glitch never breaks the capture.

        The overlays use a cycling colour palette so adjacent windows are
        visually distinct:
          index odd  → (#FF0000) red rectangle, white text
          index even → (#E67300) orange rectangle, white text
        """
        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError:
            return png_b64, width, height  # PIL not available; pass through

        try:
            raw = base64.b64decode(png_b64, validate=True)
            img = Image.open(io.BytesIO(raw))
            img.load()
        except Exception:
            return png_b64, width, height  # corrupt data; pass through

        if img.size[0] == 0 or img.size[1] == 0:
            return png_b64, width, height

        # Scale factor: the screenshot may have been captured at a different
        # resolution than the element bounds (mss captures at native display
        # resolution, so they should match — but be defensive).
        scale_x = img.size[0] / max(width, 1)
        scale_y = img.size[1] / max(height, 1)

        draw = ImageDraw.Draw(img)

        # Try to load a small fixed-font for numbering; fall back to
        # ImageDraw's default (thin, but legible).
        font = None
        try:
            font = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", size=14
            )
        except Exception:
            try:
                font = ImageFont.load_default()
            except Exception:
                pass

        colours = ("#FF0000", "#E67300")  # red, orange — alternate per index

        for el in elements:
            x, y, w, h = el.bounds
            if w <= 0 or h <= 0:
                continue
            # Scale element bounds to screenshot pixel space.
            sx = int(x * scale_x)
            sy = int(y * scale_y)
            sw = max(1, int(w * scale_x))
            sh = max(1, int(h * scale_y))
            colour = colours[(el.index - 1) % 2]

            # Rectangle around the window.
            draw.rectangle([sx, sy, sx + sw, sy + sh], outline=colour, width=3)
            # Numbered overlay badge at top-left corner.
            label = str(el.index)
            bbox = draw.textbbox((0, 0), label, font=font) if font else None
            tw = (bbox[2] - bbox[0]) if bbox else 12
            th = (bbox[3] - bbox[1]) if bbox else 10
            pad = 2
            badge_x0 = sx - pad
            badge_y0 = sy - pad
            badge_x1 = sx + tw + pad
            badge_y1 = sy + th + pad
            draw.rectangle(
                [badge_x0, badge_y0, badge_x1, badge_y1],
                fill=colour,
            )
            if font:
                draw.text((sx, sy), label, fill="white", font=font)
            else:
                draw.text((sx, sy), label, fill="white")

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        new_b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        return new_b64, img.size[0], img.size[1]

    def _screenshot_b64(self) -> Tuple[Optional[str], int, int]:
        """Return (base64_png, width, height). Prefers mss; falls back to
        ImageMagick ``import``. Returns (None, 0, 0) on failure.

        Monitor selection (mss only): mss.monitors[0] is the *bounding
        box* of all displays which, on multi-monitor setups with
        non-rectangular layouts, contains a large dark dead region
        where no monitor exists. That dead region dominates the image
        and JARVIS describes "the screen" as mostly black. To avoid
        this, we pick a real physical monitor:

          1. JARVIS_COMPUTER_USE_MONITOR env (0=bbox, 1+=physical)
          2. The monitor containing the X11 cursor (active monitor)
          3. The largest physical monitor by pixel area
          4. monitors[0] (bbox) as last resort
        """
        # Preferred: mss (no subprocess, fast).
        try:
            import mss  # type: ignore
            import mss.tools  # type: ignore

            with mss.mss() as sct:
                monitor = self._pick_screenshot_monitor(sct)
                shot = sct.grab(monitor)
                png_bytes = mss.tools.to_png(shot.rgb, shot.size)
                return (
                    base64.b64encode(png_bytes).decode("ascii"),
                    int(shot.size[0]),
                    int(shot.size[1]),
                )
        except Exception as e:
            logger.debug("mss screenshot failed (%s); trying ImageMagick import", e)

        # Fallback: ImageMagick `import -window root`.
        if shutil.which("import"):
            try:
                with tempfile.NamedTemporaryFile(suffix=".png", delete=True) as tmp:
                    rc, _out, err = self._run(
                        ["import", "-window", "root", tmp.name]
                    )
                    if rc == 0:
                        with open(tmp.name, "rb") as fh:
                            data = fh.read()
                        w, h = self._png_dimensions(data)
                        return base64.b64encode(data).decode("ascii"), w, h
                    logger.warning("ImageMagick import failed: %s", err)
            except Exception as e:  # noqa: BLE001
                logger.warning("ImageMagick screenshot failed: %s", e)
        return None, 0, 0

    def _pick_screenshot_monitor(self, sct) -> dict:
        """Pick the right `mss` monitor entry to screenshot.

        See `_screenshot_b64` docstring for the priority order. Always
        returns a valid entry — `sct.monitors[0]` is the safe fallback.
        """
        monitors = sct.monitors  # [0] = bbox, [1:] = physical displays

        override = os.environ.get("JARVIS_COMPUTER_USE_MONITOR", "").strip()
        if override:
            try:
                i = int(override)
                if 0 <= i < len(monitors):
                    return monitors[i]
            except ValueError:
                pass

        physical = monitors[1:]
        if not physical:
            return monitors[0]

        # Try the cursor's current monitor — that's the one the user is
        # interacting with right now.
        try:
            rc, out, _err = self._run(["xdotool", "getmouselocation"])
            if rc == 0 and out:
                parts = dict(
                    kv.split(":", 1) for kv in out.strip().split() if ":" in kv
                )
                cx = int(parts.get("x", "-1"))
                cy = int(parts.get("y", "-1"))
                for m in physical:
                    if (m["left"] <= cx < m["left"] + m["width"]
                        and m["top"] <= cy < m["top"] + m["height"]):
                        return m
        except Exception:
            pass

        # Last fallback: largest physical monitor by area.
        return max(physical, key=lambda m: m["width"] * m["height"])

    @staticmethod
    def _png_dimensions(data: bytes) -> Tuple[int, int]:
        """Parse width/height from a PNG IHDR header. (0,0) on failure."""
        try:
            if data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
                width = int.from_bytes(data[16:20], "big")
                height = int.from_bytes(data[20:24], "big")
                return width, height
        except Exception:
            pass
        return 0, 0

    @staticmethod
    def _crop_png(png_b64: str, orig_w: int, orig_h: int,
                  x1: int, y1: int, x2: int, y2: int) -> Tuple[Optional[str], int, int]:
        """Crop a base64 PNG to the sub-rectangle [x1,y1,x2,y2]. Returns the
        cropped PNG (base64) + new (width, height), or (None,0,0) on error.
        No downscale — the crop is at 1:1 pixel mapping (the "zoom" equivalent)."""
        try:
            raw = base64.b64decode(png_b64, validate=True)
        except Exception:
            return None, 0, 0
        try:
            from PIL import Image
            img = Image.open(io.BytesIO(raw))
            img.load()
            # Clamp to image bounds.
            x1 = max(0, min(x1, orig_w - 1))
            y1 = max(0, min(y1, orig_h - 1))
            x2 = max(x1 + 1, min(x2, orig_w))
            y2 = max(y1 + 1, min(y2, orig_h))
            cropped = img.crop((x1, y1, x2, y2))
            buf = io.BytesIO()
            cropped.save(buf, format="PNG")
            new_b64 = base64.b64encode(buf.getvalue()).decode("ascii")
            return new_b64, cropped.size[0], cropped.size[1]
        except Exception:
            return None, 0, 0

    # ── Window filter ─────────────────────────────────────────────────
    # Windows with _NET_WM_STATE_SKIP_TASKBAR are intentionally hidden
    # from the user's taskbar (panels, desktop root, tray-minimized apps)
    # and are excluded from the SOM element list — they're noise, not real
    # application windows the LLM should target.
    _SKIP_TASKBAR_ATOM = "_NET_WM_STATE_SKIP_TASKBAR"

    # Cache of {window_hex_id: (skip_flag, timestamp)} so we don't xprop
    # every window on every capture (xprop subprocess is ~2-5ms per call).
    # Entries older than _SKIP_CACHE_TTL_S seconds are rechecked — window
    # state can change between captures (e.g., dialog opens, app restarts).
    _skip_cache: dict = {}
    _SKIP_CACHE_MAX = 200
    _SKIP_CACHE_TTL_S = 30  # re-probe windows whose state is older than this

    @classmethod
    def _is_skip_taskbar(cls, wid_hex: str) -> bool:
        """Check if a window has ``_NET_WM_STATE_SKIP_TASKBAR`` via xprop.

        Cached with a TTL: window state can change (dialog opens, app
        restarts, panel toggles), so entries older than _SKIP_CACHE_TTL_S
        seconds are re-probed. If xprop fails (window destroyed between
        wmctrl and xprop calls) we assume False — the window disappears
        on the next capture anyway.
        """
        import time as _time
        now = _time.time()
        if wid_hex in cls._skip_cache:
            flag, ts = cls._skip_cache[wid_hex]
            if now - ts < cls._SKIP_CACHE_TTL_S:
                return flag
        rc, out, _err = cls._run_static(
            ["xprop", "-id", wid_hex, "_NET_WM_STATE"]
        )
        flag = rc == 0 and cls._SKIP_TASKBAR_ATOM in out
        if len(cls._skip_cache) < cls._SKIP_CACHE_MAX:
            cls._skip_cache[wid_hex] = (flag, now)
        return flag

    @staticmethod
    def _run_static(argv: list[str]) -> tuple[int, str, str]:
        """Static subprocess runner for classmethods (no instance needed)."""
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True, timeout=5
            )
            return proc.returncode, proc.stdout or "", proc.stderr or ""
        except Exception:
            return -1, "", ""

    def _enumerate_windows(self, app: Optional[str]) -> List[UIElement]:
        """Best-effort window list via ``wmctrl -lpG`` (id, desktop, pid,
        geometry, host, title). Falls back to empty when wmctrl is absent.

        Filters out known X11 pseudo-windows (desktop root, panel, task
        bars, popup windows with no title) so the SOM element list shows
        only real application windows — the LLM sees fewer, more relevant
        targets and wastes fewer tokens on noise.
        """
        if not shutil.which("wmctrl"):
            return []
        rc, out, _err = self._run(["wmctrl", "-lpG"])
        if rc != 0 or not out:
            return []
        elements: List[UIElement] = []
        idx = 0
        for line in out.splitlines():
            # 0xID  desktop  pid  x  y  w  h  host  title...
            m = re.match(
                r"^(0x[0-9a-fA-F]+)\s+(-?\d+)\s+(\d+)\s+(-?\d+)\s+(-?\d+)\s+(\d+)\s+(\d+)\s+\S+\s+(.*)$",
                line,
            )
            if not m:
                continue
            wid = int(m.group(1), 16)
            pid = int(m.group(3))
            x, y, w, h = (int(m.group(i)) for i in (4, 5, 6, 7))
            title = m.group(8)
            if app and app.lower() not in title.lower():
                continue
            # Filter X11 pseudo-windows (panels, root-desktop, tray apps).
            # Any window with _NET_WM_STATE_SKIP_TASKBAR is hidden from the
            # taskbar intentionally — don't show it in the element list.
            if self._is_skip_taskbar(m.group(1)):
                continue
            idx += 1
            elements.append(
                UIElement(
                    index=idx,
                    role="window",
                    label=title,
                    bounds=(x, y, w, h),
                    app=title,
                    pid=pid,
                    window_id=wid,
                )
            )
        return elements

    # ── Pointer ────────────────────────────────────────────────────
    def _resolve_element(
        self, element: Optional[int]
    ) -> Tuple[Optional[int], Optional[int], Optional[str]]:
        """Resolve a 1-based element index to pixel (cx, cy) using the last capture's
        element list, or return (None, None, error) on failure.

        The element list comes from ``_enumerate_windows`` — window-level
        elements with bounds (x, y, w, h). This method returns the *center* of
        the element's bounding box, which is the safest click/scroll target.
        """
        if element is None:
            return None, None, None  # no element requested, caller uses raw xy
        if not self._last_elements:
            return (
                None,
                None,
                "no element list available — call capture(mode='som') or "
                "capture(mode='ax') first to build the element index.",
            )
        # Element indices are 1-based (seen in the SOM overlay).
        idx = int(element)
        if idx < 1 or idx > len(self._last_elements):
            return (
                None,
                None,
                f"element index {idx} is out of range — the last capture "
                f"has {len(self._last_elements)} elements (1-{len(self._last_elements)}). "
                "Recapture with capture(mode='som') to refresh.",
            )
        el = self._last_elements[idx - 1]
        cx, cy = el.center()
        return int(cx), int(cy), None

    def _resolve_xy(
        self,
        element: Optional[int],
        x: Optional[int],
        y: Optional[int],
    ) -> Tuple[Optional[int], Optional[int], Optional[str]]:
        """Resolve a click/scroll target to (x, y) or an error string.

        When *element* is given, the element-index (from a SOM capture) takes
        priority and the *x* / *y* coordinates are ignored. When *element* is
        None, raw pixel coordinates are used.

        Returns (x, y, None) on success or (None, None, error) on failure.
        """
        if element is not None:
            ex, ey, err = self._resolve_element(element)
            if err:
                return None, None, err
            return ex, ey, None
        if x is None or y is None:
            return None, None, "missing coordinate=[x, y]."
        return int(x), int(y), None

    def click(
        self,
        *,
        element: Optional[int] = None,
        x: Optional[int] = None,
        y: Optional[int] = None,
        button: str = "left",
        click_count: int = 1,
        modifiers: Optional[List[str]] = None,
    ) -> ActionResult:
        rx, ry, err = self._resolve_xy(element, x, y)
        if err:
            return ActionResult(ok=False, action="click", message=err)
        btn = _BUTTON_NUM.get(button, "1")
        held = self._press_modifiers(modifiers)
        try:
            self._xdo("mousemove", "--sync", str(rx), str(ry))
            argv = ["click"]
            if click_count and click_count > 1:
                argv += ["--repeat", str(int(click_count))]
            argv += [btn]
            rc, _out, e = self._xdo(*argv)
        finally:
            self._release_modifiers(held)
        ok = rc == 0
        return ActionResult(
            ok=ok,
            action="click",
            message=("" if ok else f"xdotool click failed: {e}"),
            meta={"x": rx, "y": ry, "button": button, "click_count": click_count},
        )

    def mouse_down(
        self,
        *,
        element: Optional[int] = None,
        x: Optional[int] = None,
        y: Optional[int] = None,
        button: str = "left",
        modifiers: Optional[List[str]] = None,
    ) -> ActionResult:
        """Press a mouse button without releasing — raw mousedown, needed for
        canvas drawing, drag-handle interactions, and games."""
        rx, ry, err = self._resolve_xy(element, x, y)
        if err:
            return ActionResult(ok=False, action="mouse_down", message=err)
        btn = _BUTTON_NUM.get(button, "1")
        held = self._press_modifiers(modifiers)
        try:
            self._xdo("mousemove", "--sync", str(rx), str(ry))
            rc, _out, e = self._xdo("mousedown", btn)
        finally:
            self._release_modifiers(held)
        ok = rc == 0
        return ActionResult(
            ok=ok, action="mouse_down",
            message=("" if ok else f"xdotool mousedown failed: {e}"),
            meta={"x": rx, "y": ry, "button": button},
        )

    def mouse_up(
        self,
        *,
        element: Optional[int] = None,
        x: Optional[int] = None,
        y: Optional[int] = None,
        button: str = "left",
        modifiers: Optional[List[str]] = None,
    ) -> ActionResult:
        """Release a previously-pressed mouse button — pairs with mouse_down."""
        # mouse_up is typically relative to current cursor position; coords are
        # optional (if given, move there first before releasing).
        if element is not None or (x is not None and y is not None):
            rx, ry, err = self._resolve_xy(element, x, y)
            if err:
                return ActionResult(ok=False, action="mouse_up", message=err)
            self._xdo("mousemove", "--sync", str(rx), str(ry))
        btn = _BUTTON_NUM.get(button, "1")
        held = self._press_modifiers(modifiers)
        try:
            rc, _out, e = self._xdo("mouseup", btn)
        finally:
            self._release_modifiers(held)
        ok = rc == 0
        return ActionResult(
            ok=ok, action="mouse_up",
            message=("" if ok else f"xdotool mouseup failed: {e}"),
            meta={"button": button},
        )

    def drag(
        self,
        *,
        from_element: Optional[int] = None,
        to_element: Optional[int] = None,
        from_xy: Optional[Tuple[int, int]] = None,
        to_xy: Optional[Tuple[int, int]] = None,
        button: str = "left",
        modifiers: Optional[List[str]] = None,
    ) -> ActionResult:
        # Resolve element indices to pixel coordinates when given.
        if from_element is not None:
            fx, fy, err = self._resolve_element(from_element)
            if err:
                return ActionResult(
                    ok=False, action="drag",
                    message=f"from_element: {err}",
                )
            from_xy = (fx, fy)
        if to_element is not None:
            tx, ty, err = self._resolve_element(to_element)
            if err:
                return ActionResult(
                    ok=False, action="drag",
                    message=f"to_element: {err}",
                )
            to_xy = (tx, ty)
        if not from_xy or not to_xy:
            return ActionResult(
                ok=False,
                action="drag",
                message="drag requires from_coordinate and to_coordinate, "
                "or from_element and to_element.",
            )
        btn = _BUTTON_NUM.get(button, "1")
        held = self._press_modifiers(modifiers)
        try:
            self._xdo("mousemove", "--sync", str(int(from_xy[0])), str(int(from_xy[1])))
            self._xdo("mousedown", btn)
            self._xdo("mousemove", "--sync", str(int(to_xy[0])), str(int(to_xy[1])))
            rc, _out, e = self._xdo("mouseup", btn)
        finally:
            self._release_modifiers(held)
        ok = rc == 0
        return ActionResult(
            ok=ok,
            action="drag",
            message=("" if ok else f"xdotool drag failed: {e}"),
            meta={"from": list(from_xy), "to": list(to_xy), "button": button},
        )

    def scroll(
        self,
        *,
        direction: str,
        amount: int = 3,
        element: Optional[int] = None,
        x: Optional[int] = None,
        y: Optional[int] = None,
        modifiers: Optional[List[str]] = None,
    ) -> ActionResult:
        btn = _SCROLL_BUTTON.get(direction)
        if btn is None:
            return ActionResult(
                ok=False, action="scroll",
                message=f"bad direction {direction!r}; use up|down|left|right.",
            )
        # Resolve element index first (overrides raw x/y).
        if element is not None:
            ex, ey, err = self._resolve_element(element)
            if err:
                return ActionResult(
                    ok=False, action="scroll", message=f"element: {err}",
                )
            x, y = ex, ey
        ticks = max(1, min(50, int(amount)))
        held = self._press_modifiers(modifiers)
        try:
            if x is not None and y is not None:
                self._xdo("mousemove", "--sync", str(int(x)), str(int(y)))
            rc, _out, e = self._xdo("click", "--repeat", str(ticks), btn)
        finally:
            self._release_modifiers(held)
        ok = rc == 0
        return ActionResult(
            ok=ok,
            action="scroll",
            message=("" if ok else f"xdotool scroll failed: {e}"),
            meta={"direction": direction, "amount": ticks},
        )

    # ── Keyboard ───────────────────────────────────────────────────
    def type_text(self, text: str) -> ActionResult:
        # --clearmodifiers avoids a held Shift/Ctrl corrupting the typed text.
        rc, _out, e = self._xdo("type", "--clearmodifiers", "--", text)
        ok = rc == 0
        return ActionResult(
            ok=ok,
            action="type",
            message=("" if ok else f"xdotool type failed: {e}"),
            meta={"chars": len(text)},
        )

    def key(self, keys: str) -> ActionResult:
        keysym = parse_key_combo_to_xdotool(keys)
        if not keysym:
            return ActionResult(
                ok=False, action="key", message=f"could not parse key combo {keys!r}.",
            )
        rc, _out, e = self._xdo("key", "--clearmodifiers", keysym)
        ok = rc == 0
        return ActionResult(
            ok=ok,
            action="key",
            message=("" if ok else f"xdotool key failed: {e}"),
            meta={"keys": keysym},
        )

    def hold_key(self, keys: str, seconds: float = 1.0) -> ActionResult:
        """Hold a key (or combo) for *seconds* then release — needed for game
        controls, long-press shortcuts, and accessibility features."""
        keysym = parse_key_combo_to_xdotool(keys)
        if not keysym:
            return ActionResult(
                ok=False, action="hold_key",
                message=f"could not parse key combo {keys!r}.",
            )
        secs = max(0.05, min(float(seconds), 30.0))
        rc, _out, e = self._xdo("keydown", "--clearmodifiers", keysym)
        if rc != 0:
            return ActionResult(
                ok=False, action="hold_key",
                message=f"xdotool keydown failed: {e}",
            )
        time.sleep(secs)
        rc2, _out2, e2 = self._xdo("keyup", "--clearmodifiers", keysym)
        ok = rc2 == 0
        return ActionResult(
            ok=ok, action="hold_key",
            message=(f"held {keysym!r} for {secs:.2f}s" if ok
                     else f"xdotool keyup failed: {e2}"),
            meta={"keys": keysym, "seconds": secs},
        )

    def key_down(self, keys: str) -> ActionResult:
        """Press (and hold) a key or combo — raw keydown, no auto-release.
        Pairs with key_up for composable modifier sequences, e.g.
        key_down('ctrl') → click(...) → key_up('ctrl') for Ctrl+click
        multi-select."""
        keysym = parse_key_combo_to_xdotool(keys)
        if not keysym:
            return ActionResult(
                ok=False, action="key_down",
                message=f"could not parse key combo {keys!r}.",
            )
        rc, _out, e = self._xdo("keydown", "--clearmodifiers", keysym)
        ok = rc == 0
        return ActionResult(
            ok=ok, action="key_down",
            message=(f"key_down {keysym!r}" if ok else f"xdotool keydown failed: {e}"),
            meta={"keys": keysym},
        )

    def key_up(self, keys: str) -> ActionResult:
        """Release a previously-pressed key or combo — pairs with key_down."""
        keysym = parse_key_combo_to_xdotool(keys)
        if not keysym:
            return ActionResult(
                ok=False, action="key_up",
                message=f"could not parse key combo {keys!r}.",
            )
        rc, _out, e = self._xdo("keyup", "--clearmodifiers", keysym)
        ok = rc == 0
        return ActionResult(
            ok=ok, action="key_up",
            message=(f"key_up {keysym!r}" if ok else f"xdotool keyup failed: {e}"),
            meta={"keys": keysym},
        )

    # ── Introspection ──────────────────────────────────────────────
    def list_apps(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for el in self._enumerate_windows(None):
            out.append(
                {
                    "window_id": el.window_id,
                    "pid": el.pid,
                    "title": el.label,
                    "bounds": list(el.bounds),
                }
            )
        return out

    # ── App discovery: scan .desktop files ─────────────────────────

    # %-codes that desktop-file Exec lines use as argument placeholders.
    # Stripped to get the bare binary name.
    _DESKTOP_EXEC_STRIP = re.compile(
        r"(%[fFuUdDnNickvm]|%[@]|%\S)", re.I
    )

    @classmethod
    def _parse_desktop_file(cls, path: Path) -> Optional[Dict[str, Any]]:
        """Parse a single .desktop file, returning {name, command, category, icon}
        or None for NoDisplay/Hidden entries."""
        try:
            text = path.read_text(errors="ignore")
        except Exception:
            return None
        # Only parse the [Desktop Entry] section.
        in_entry = False
        name = cmd = categories = icon = ""
        hidden = nodisplay = False
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("["):
                in_entry = line.strip() == "[Desktop Entry]"
                continue
            if not in_entry:
                continue
            if "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip().lower()
            val = val.strip()
            if key == "name" and not name:
                name = val
            elif key == "exec" and not cmd:
                cmd = val
            elif key == "categories":
                categories = val
            elif key == "icon" and not icon:
                icon = val
            elif key == "nodisplay":
                nodisplay = val.lower() == "true"
            elif key == "hidden":
                hidden = val.lower() == "true"
        if hidden or nodisplay or not name:
            return None
        # Extract the bare command: strip path args and %-codes.
        if cmd:
            # Split on first space to get the binary
            parts = cmd.split(None, 1)
            binary = parts[0] if parts else cmd
            # Resolve full path if relative
            if "/" not in binary:
                resolved = shutil.which(binary)
                if resolved:
                    binary = resolved
            # Clean up %-codes from the rest
            if len(parts) > 1:
                args = cls._DESKTOP_EXEC_STRIP.sub("", parts[1]).strip()
                cmd = f"{binary} {args}" if args else binary
            else:
                cmd = binary
        # Derive a human-readable category label.
        cat = ""
        cats_lower = categories.lower()
        if "filemanager" in cats_lower or "file-manager" in cats_lower:
            cat = "file_manager"
        elif "webbrowser" in cats_lower or "web-browser" in cats_lower:
            cat = "browser"
        elif "terminalemulator" in cats_lower or "terminal-emulator" in cats_lower:
            cat = "terminal"
        elif "settings" in cats_lower:
            cat = "settings"
        elif "texteditor" in cats_lower or "text-editor" in cats_lower:
            cat = "text_editor"
        elif "development" in cats_lower or "ide" in cats_lower:
            cat = "development"
        elif "audio" in cats_lower or "video" in cats_lower or "player" in cats_lower:
            cat = "media"
        elif "graphics" in cats_lower or "image" in cats_lower:
            cat = "graphics"
        elif "office" in cats_lower or "spreadsheet" in cats_lower or "word" in cats_lower:
            cat = "office"
        elif "chat" in cats_lower or "messaging" in cats_lower or "im" in cats_lower:
            cat = "chat"
        elif "network" in cats_lower:
            cat = "network"
        return {
            "name": name,
            "command": cmd.split()[0] if cmd else "",
            "full_command": cmd,
            "category": cat,
            "icon": icon,
        }

    def list_available_apps(self) -> List[Dict[str, Any]]:
        """Scan .desktop files to discover installed applications. Returns
        a list of {name, command, category, icon} for each visible desktop
        entry. Gives the supervisor ground truth about what's actually
        installed instead of guessing from training-data defaults."""
        seen: set[str] = set()
        results: List[Dict[str, Any]] = []
        search_dirs = [
            Path("/usr/share/applications"),
            Path.home() / ".local" / "share" / "applications",
        ]
        for search_dir in search_dirs:
            if not search_dir.is_dir():
                continue
            for entry in sorted(search_dir.iterdir()):
                if not entry.suffix == ".desktop":
                    continue
                info = self._parse_desktop_file(entry)
                if info is None:
                    continue
                # Dedupe by command — same app may have multiple .desktop files.
                key = info["command"]
                if key and key not in seen:
                    seen.add(key)
                    results.append(info)
        return results

    # ── App launch ──────────────────────────────────────────────────

    def launch_app(self, command: str) -> ActionResult:
        """Launch *command* via ``setsid`` so it detaches from the voice-agent
        process and survives the turn. Resolves bare names via ``which`` first.
        Returns ok/error — does NOT focus the window (caller handles that)."""
        cmd = command.strip()
        if not cmd:
            return ActionResult(ok=False, action="launch",
                               message="launch requires a non-empty command")
        # Resolve bare binary names to full path so setsid can find them.
        if "/" not in cmd:
            resolved = shutil.which(cmd)
            if resolved:
                cmd = resolved
        rc, _out, err = self._run(["setsid", cmd, "&"])
        ok = rc == 0
        return ActionResult(
            ok=ok, action="launch",
            message=(f"launched {command!r}" if ok
                     else f"launch failed: {err}"),
            meta={"command": command},
        )

    def move_cursor(
        self,
        *,
        element: Optional[int] = None,
        x: Optional[int] = None,
        y: Optional[int] = None,
    ) -> ActionResult:
        """Move the mouse cursor without clicking — needed for hover effects,
        tooltip triggers, and positioning before a separate click action."""
        if element is not None:
            target = self._resolve_element(element)
            if target is None:
                return ActionResult(
                    ok=False, action="mouse_move",
                    message=f"element {element} not found in cache — call capture first",
                )
            x, y = target
        if x is None or y is None:
            return ActionResult(
                ok=False, action="mouse_move",
                message="mouse_move requires element or coordinate=[x, y]",
            )
        rc, _o, e = self._run(["xdotool", "mousemove", str(x), str(y)])
        ok = rc == 0
        return ActionResult(
            ok=ok, action="mouse_move",
            message=(f"cursor moved to ({x}, {y})" if ok
                     else f"mousemove failed: {e}"),
            meta={"x": x, "y": y},
        )

    def get_cursor_position(self) -> ActionResult:
        """Return the current mouse cursor coordinates."""
        rc, out, e = self._run(["xdotool", "getmouselocation", "--shell"])
        if rc != 0:
            return ActionResult(
                ok=False, action="cursor_position",
                message=f"xdotool getmouselocation failed: {e}",
            )
        x = y = None
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("X="):
                try: x = int(line.split("=", 1)[1])
                except ValueError: pass
            elif line.startswith("Y="):
                try: y = int(line.split("=", 1)[1])
                except ValueError: pass
        if x is not None and y is not None:
            return ActionResult(
                ok=True, action="cursor_position",
                message=f"cursor at ({x}, {y})",
                meta={"x": x, "y": y},
            )
        return ActionResult(
            ok=False, action="cursor_position",
            message=f"could not parse position from: {out[:100]}",
        )

    def focus_app(self, app: str, raise_window: bool = False) -> ActionResult:
        """Activate the first window whose title matches *app* (substring).

        On X11 there's no background-focus primitive like the macOS backend's;
        ``wmctrl -a`` activates (and by WM policy usually raises) the window.
        ``raise_window`` is accepted for signature parity but does not change
        behavior here.
        """
        if not shutil.which("wmctrl"):
            return ActionResult(
                ok=False, action="focus_app",
                message="wmctrl not available to focus windows.",
            )
        rc, _out, e = self._run(["wmctrl", "-a", app])
        ok = rc == 0
        return ActionResult(
            ok=ok,
            action="focus_app",
            message=(f"activated window matching {app!r}" if ok
                     else f"no window matched {app!r}: {e}"),
            meta={"app": app},
        )


# ---------------------------------------------------------------------------
# Test / CI stub backend
# ---------------------------------------------------------------------------


class NoopBackend(ComputerUseBackend):
    """Records calls; returns trivial results. Never touches X11. Used by
    tests and selectable via ``JARVIS_COMPUTER_USE_BACKEND=noop``."""

    def __init__(self) -> None:
        self.calls: List[Tuple[str, Dict[str, Any]]] = []
        self._started = False

    def start(self) -> None:
        self._started = True

    def stop(self) -> None:
        self._started = False

    def is_available(self) -> bool:
        return True

    def capture(self, mode: str = "som", app: Optional[str] = None,
                region: Optional[List[int]] = None) -> CaptureResult:
        self.calls.append(("capture", {"mode": mode, "app": app, "region": region}))
        return CaptureResult(mode=mode, width=1024, height=768, png_b64=None,
                             elements=[], app=app or "", window_title="")

    def click(self, **kw: Any) -> ActionResult:
        self.calls.append(("click", kw))
        return ActionResult(ok=True, action="click")

    def drag(self, **kw: Any) -> ActionResult:
        self.calls.append(("drag", kw))
        return ActionResult(ok=True, action="drag")

    def scroll(self, **kw: Any) -> ActionResult:
        self.calls.append(("scroll", kw))
        return ActionResult(ok=True, action="scroll")

    def type_text(self, text: str) -> ActionResult:
        self.calls.append(("type", {"text": text}))
        return ActionResult(ok=True, action="type")

    def key(self, keys: str) -> ActionResult:
        self.calls.append(("key", {"keys": keys}))
        return ActionResult(ok=True, action="key")

    def list_apps(self) -> List[Dict[str, Any]]:
        self.calls.append(("list_apps", {}))
        return []

    def list_available_apps(self) -> List[Dict[str, Any]]:
        self.calls.append(("list_available_apps", {}))
        return []

    def launch_app(self, command: str) -> ActionResult:
        self.calls.append(("launch", {"command": command}))
        return ActionResult(ok=True, action="launch",
                           meta={"command": command})

    def focus_app(self, app: str, raise_window: bool = False) -> ActionResult:
        self.calls.append(("focus_app", {"app": app, "raise": raise_window}))
        return ActionResult(ok=True, action="focus_app")

    def move_cursor(self, *, element=None, x=None, y=None) -> ActionResult:
        self.calls.append(("move_cursor", {"element": element, "x": x, "y": y}))
        return ActionResult(ok=True, action="mouse_move",
                           meta={"x": x or 0, "y": y or 0})

    def get_cursor_position(self) -> ActionResult:
        self.calls.append(("get_cursor_position", {}))
        return ActionResult(ok=True, action="cursor_position",
                           meta={"x": 500, "y": 300})

    def mouse_down(self, *, element=None, x=None, y=None, button="left",
                   modifiers=None) -> ActionResult:
        self.calls.append(("mouse_down", {"element": element, "x": x, "y": y,
                                           "button": button, "modifiers": modifiers}))
        return ActionResult(ok=True, action="mouse_down",
                           meta={"x": x or 0, "y": y or 0, "button": button})

    def mouse_up(self, *, element=None, x=None, y=None, button="left",
                 modifiers=None) -> ActionResult:
        self.calls.append(("mouse_up", {"element": element, "x": x, "y": y,
                                         "button": button, "modifiers": modifiers}))
        return ActionResult(ok=True, action="mouse_up", meta={"button": button})

    def hold_key(self, keys: str, seconds: float = 1.0) -> ActionResult:
        self.calls.append(("hold_key", {"keys": keys, "seconds": seconds}))
        return ActionResult(ok=True, action="hold_key",
                           meta={"keys": keys, "seconds": seconds})

    def key_down(self, keys: str) -> ActionResult:
        self.calls.append(("key_down", {"keys": keys}))
        return ActionResult(ok=True, action="key_down", meta={"keys": keys})

    def key_up(self, keys: str) -> ActionResult:
        self.calls.append(("key_up", {"keys": keys}))
        return ActionResult(ok=True, action="key_up", meta={"keys": keys})
