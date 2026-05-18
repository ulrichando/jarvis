"""Computer-use backend primitives — see & act on the Linux X11 desktop.

Spec: docs/superpowers/specs/2026-05-18-jarvis-computer-use-parity-design.md §4

Backend-swappable: this module wraps the X11-specific tools (mss for
screenshot, xdotool for input) behind a stable interface so future
Wayland support (ydotool / wtype / grim) can drop in by swapping
imports without touching the loop driver.

All ops raise BackendError on failure. Never silent-fail — the loop
needs to see backend failures so it can replan.
"""
from __future__ import annotations

import asyncio
import io
import logging
import shutil
from typing import Optional


logger = logging.getLogger("jarvis.computer_backend")


__all__ = [
    "BackendError",
    "take_screenshot",
    "scale_for_model",
    "click",
    "double_click",
    "right_click",
    "drag",
    "mouse_move",
    "type_text",
    "key_combo",
    "scroll",
]


class BackendError(Exception):
    """Raised when an mss / xdotool / scrot call fails."""


# Anthropic's MAX_SCALING_TARGETS — port verbatim from
# computer_use_demo/tools/computer.py. Picked by aspect-ratio match.
_SCALING_TARGETS: list[tuple[str, int, int]] = [
    ("XGA",   1024, 768),
    ("WXGA",  1280, 800),
    ("FWXGA", 1366, 768),
]


def _pick_scaling_target(width: int, height: int) -> tuple[int, int]:
    """Pick the MAX_SCALING_TARGETS entry whose aspect ratio is closest
    to the source. Anthropic's docs are explicit that picking
    aspect-ratio-closest minimizes coordinate distortion."""
    source_ratio = width / height if height else 1.0
    best: Optional[tuple[float, int, int]] = None
    for _name, w, h in _SCALING_TARGETS:
        ratio = w / h
        delta = abs(source_ratio - ratio)
        if best is None or delta < best[0]:
            best = (delta, w, h)
    assert best is not None
    return (best[1], best[2])


# Module-level state for mss availability — set by _init_mss(), read by
# take_screenshot(). Lazy so import doesn't fail when mss isn't installed
# yet (we fall back to scrot).
_mss_module = None
_mss_available: bool = False


def _init_mss() -> None:
    global _mss_module, _mss_available
    if _mss_available:
        return
    try:
        import mss as _m
        _mss_module = _m.mss
        _mss_available = True
    except Exception as e:
        logger.warning(
            f"[computer_backend] mss unavailable ({e}); "
            "falling back to scrot for screenshots"
        )
        _mss_available = False


_init_mss()


async def take_screenshot() -> bytes:
    """Capture the primary display as PNG bytes.

    Prefers mss (~10 ms). Falls back to `scrot -p` (~200 ms) when mss
    is unavailable. Returns the PNG bytes directly so callers can
    pass to PIL / Anthropic without a temp file.

    Raises BackendError on any failure.
    """
    if _mss_available:
        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, _take_screenshot_mss)
        except Exception as e:
            logger.warning(f"[computer_backend] mss failed: {e}; trying scrot")
    # scrot fallback
    return await _take_screenshot_scrot()


def _take_screenshot_mss() -> bytes:
    """Sync helper: grab primary monitor via mss, encode PNG."""
    from PIL import Image
    with _mss_module() as sct:
        # monitors[0] is the union of all monitors; monitors[1] is the
        # primary. We pin to primary per spec §6.E.
        mon = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
        frame = sct.grab(mon)
        img = Image.frombytes(
            "RGB", (frame.size.width, frame.size.height),
            frame.bgra, "raw", "BGRX"
        )
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=False)
        return buf.getvalue()


async def _take_screenshot_scrot() -> bytes:
    """scrot fallback. Writes to a temp file and reads back."""
    import tempfile
    import os
    if not shutil.which("scrot"):
        raise BackendError("neither mss nor scrot is available for screenshot")
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        path = f.name
    try:
        proc = await asyncio.create_subprocess_exec(
            "scrot", "-p", path,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, err = await asyncio.wait_for(proc.communicate(), timeout=5.0)
        if proc.returncode != 0:
            raise BackendError(
                f"scrot returncode={proc.returncode}: {err.decode(errors='replace')[:200]}"
            )
        with open(path, "rb") as fh:
            data = fh.read()
        if not data:
            raise BackendError("scrot produced empty file")
        return data
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def scale_for_model(png: bytes) -> tuple[bytes, float, float]:
    """Resize the screenshot for model input, return (scaled, sx, sy)
    where sx/sy multiply model-emitted coords to get native coords.

    Picks a MAX_SCALING_TARGETS entry by closest aspect ratio. If the
    source is already <= the target on both axes, returns the original
    bytes with sx=sy=1.0.
    """
    from PIL import Image
    img = Image.open(io.BytesIO(png))
    src_w, src_h = img.size
    tgt_w, tgt_h = _pick_scaling_target(src_w, src_h)
    if src_w <= tgt_w and src_h <= tgt_h:
        return png, 1.0, 1.0
    scaled = img.resize((tgt_w, tgt_h), Image.LANCZOS)
    buf = io.BytesIO()
    scaled.save(buf, format="PNG", optimize=False)
    return buf.getvalue(), src_w / tgt_w, src_h / tgt_h


# ═══════════════════════════════════════════════════════════════════════════════
# Input Operations (xdotool wrappers)
# ═══════════════════════════════════════════════════════════════════════════════


async def _run_xdotool(*args: str) -> None:
    """Run xdotool with the given args. Raises BackendError on
    non-zero exit or missing binary."""
    if not shutil.which("xdotool"):
        raise BackendError("xdotool is not installed; run `apt install xdotool`")
    proc = await asyncio.create_subprocess_exec(
        "xdotool", *args,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, err = await asyncio.wait_for(proc.communicate(), timeout=5.0)
    if proc.returncode != 0:
        raise BackendError(
            f"xdotool returncode={proc.returncode}: "
            f"{err.decode(errors='replace')[:200]}"
        )


_BUTTON_NUM = {"left": "1", "middle": "2", "right": "3"}


async def click(
    x: int, y: int, button: str = "left", modifiers: list[str] = []
) -> None:
    """Move cursor to (x,y) and click with the named button. Optional
    modifier keys (shift/ctrl/alt/super) are held during the click."""
    btn = _BUTTON_NUM.get(button, "1")
    # --clearmodifiers undoes any sticky modifiers before our op; we add
    # our own modifiers explicitly via keydown/keyup so the click only
    # sees what we asked for.
    if modifiers:
        keyspec = "+".join(modifiers)
        await _run_xdotool(
            "mousemove", "--sync", str(x), str(y),
            "keydown", keyspec,
            "click", "--clearmodifiers", btn,
            "keyup", keyspec,
        )
    else:
        await _run_xdotool(
            "mousemove", "--sync", str(x), str(y),
            "click", "--clearmodifiers", btn,
        )


async def double_click(x: int, y: int) -> None:
    await _run_xdotool(
        "mousemove", "--sync", str(x), str(y),
        "click", "--repeat", "2", "--delay", "50", "--clearmodifiers", "1",
    )


async def right_click(x: int, y: int) -> None:
    await click(x, y, button="right")


async def drag(
    start: tuple[int, int], end: tuple[int, int]
) -> None:
    sx, sy = start
    ex, ey = end
    await _run_xdotool(
        "mousemove", "--sync", str(sx), str(sy),
        "mousedown", "1",
        "mousemove", "--sync", str(ex), str(ey),
        "mouseup", "1",
    )


async def mouse_move(x: int, y: int) -> None:
    await _run_xdotool("mousemove", "--sync", str(x), str(y))


async def type_text(text: str, delay_ms: int = 12) -> None:
    """Type the given text at the current cursor position. Delay between
    keystrokes is 12ms by default (matches Anthropic's reference)."""
    await _run_xdotool("type", "--delay", str(delay_ms), text)


async def key_combo(combo: str) -> None:
    """Press a key combination like 'ctrl+s', 'Return', 'Escape'."""
    await _run_xdotool("key", "--clearmodifiers", combo)


async def scroll(
    x: int, y: int, direction: str, amount: int
) -> None:
    """Scroll at (x,y) by `amount` clicks in `direction`
    (up/down/left/right)."""
    # xdotool scroll wheel: button 4=up, 5=down, 6=left, 7=right.
    btn_map = {"up": "4", "down": "5", "left": "6", "right": "7"}
    btn = btn_map.get(direction)
    if btn is None:
        raise BackendError(f"unknown scroll direction: {direction}")
    await _run_xdotool(
        "mousemove", "--sync", str(x), str(y),
        "click", "--repeat", str(amount), "--clearmodifiers", btn,
    )
