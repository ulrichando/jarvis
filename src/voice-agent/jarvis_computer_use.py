"""
JARVIS desktop computer-use: Gemini vision + xdotool control.

Tools (all @function_tool, registered in jarvis_agent.py):
    computer_use  — start session; first screenshot → Gemini describe
    computer_stop — end session
    click         — xdotool click at (x, y)
    type_text     — xdotool type + optional Enter
    scroll        — xdotool scroll at (x, y)
    drag          — xdotool drag from→to
    key_press     — xdotool key combination (e.g. "ctrl+t")
    wait          — sleep N ms, then re-describe screen
    screenshot    — one-shot screenshot + Gemini describe (no session needed)

Safety guards:
    _FAILURE_LIMIT consecutive failures → stop and explain
    _STALL_TIMEOUT_S with no visible UI change → stop and explain
"""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass, field

from livekit.agents import function_tool

logger = logging.getLogger("jarvis-computer-use")

GEMINI_MODEL = "gemini-2.0-flash"
GEMINI_SCREEN_PROMPT = (
    "You are helping a voice assistant control a desktop computer. "
    "Describe the current screen state: what application is open, all "
    "visible UI elements (buttons, text fields, menus, links), and their "
    "approximate pixel coordinates (x, y from top-left corner). "
    "Be specific and concise — the assistant will decide what to click or type."
)

_FAILURE_LIMIT = 3
_STALL_TIMEOUT_S = 30.0


class ComputerUseError(RuntimeError):
    pass


@dataclass
class _Session:
    task: str
    started_at: float = field(default_factory=time.monotonic)
    consecutive_failures: int = 0
    last_description: str = ""
    last_change_at: float = field(default_factory=time.monotonic)


_active_session: _Session | None = None


# ── Gemini ────────────────────────────────────────────────────────────


def _get_gemini_client():
    """Return a google.genai Client. Raise ComputerUseError if key missing."""
    from google import genai
    key = os.environ.get("GOOGLE_API_KEY", "")
    if not key:
        raise ComputerUseError("GOOGLE_API_KEY not set in environment")
    return genai.Client(api_key=key)


def _take_screenshot() -> bytes:
    """Take a full-screen PNG via scrot, return the bytes."""
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        path = f.name
    subprocess.run(
        ["scrot", "-z", path],
        check=True,
        timeout=5,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    with open(path, "rb") as f:
        return f.read()


async def _gemini_describe(png_bytes: bytes) -> str:
    """Send PNG bytes to Gemini vision, return UI description string."""
    from google.genai import types as genai_types
    client = _get_gemini_client()
    loop = asyncio.get_running_loop()

    def _call() -> str:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[
                genai_types.Part.from_bytes(data=png_bytes, mime_type="image/png"),
                GEMINI_SCREEN_PROMPT,
            ],
        )
        return response.text or "(no description returned)"

    return await loop.run_in_executor(None, _call)


async def _screenshot_and_describe() -> str:
    """Take screenshot, send to Gemini, return description."""
    png = _take_screenshot()
    return await _gemini_describe(png)


# ── Session safety guards ─────────────────────────────────────────────


def _check_guards() -> None:
    """Raise ComputerUseError if a safety limit is exceeded."""
    if _active_session is None:
        return
    if _active_session.consecutive_failures >= _FAILURE_LIMIT:
        raise ComputerUseError(
            f"Stopping after {_FAILURE_LIMIT} consecutive failures. "
            "The computer is not responding. Tell the user what you tried."
        )
    elapsed = time.monotonic() - _active_session.last_change_at
    if elapsed >= _STALL_TIMEOUT_S:
        raise ComputerUseError(
            f"Stopping: no visible UI change in {int(elapsed)}s. "
            "The screen appears stuck. Tell the user what you last saw."
        )


def _record_success(description: str) -> None:
    """Reset failure counter; update last_change_at if screen changed."""
    if _active_session is None:
        return
    _active_session.consecutive_failures = 0
    if description != _active_session.last_description:
        _active_session.last_change_at = time.monotonic()
    _active_session.last_description = description


def _record_failure() -> None:
    if _active_session is not None:
        _active_session.consecutive_failures += 1
