"""Vision-backend primitives: Gemini Flash + Ollama dispatch.

Low-level image-describe pipeline shared by every "what's on the
screen?" feature (computer_use perception loop, screenshot tool,
live_screen, watch_screen, webcam_capture, face_id cross-checks).

Backend selection:
  * `JARVIS_VISION_BACKEND=auto` (default) — use Ollama if reachable
    on localhost, else Gemini.
  * `JARVIS_VISION_BACKEND=ollama` — force on-device (free, private,
    slower ~5-15 s/frame on RTX 2060 6GB).
  * `JARVIS_VISION_BACKEND=gemini` — force cloud (fast, requires
    GOOGLE_API_KEY + internet).

Auto-fallback: when Ollama is configured/reachable but the call
fails for any reason, this module silently falls back to Gemini so
a misbehaving local model can't break voice replies.

Hoisted from `tools/computer_use.py` 2026-05-10 (Step 7 of the
audit). The higher-level screenshot capture + screen-comparison
helpers (`_take_screenshot`, `_screenshot_and_describe`,
`_live_screen_polling`) stay in computer_use because they bind to
session state.
"""
from __future__ import annotations

import asyncio
import logging
import os


logger = logging.getLogger("jarvis.computer_use")


__all__ = [
    "VISION_BACKEND",
    "OLLAMA_VISION_MODEL",
    "OLLAMA_URL",
    "GEMINI_MODEL",
    "GEMINI_SCREEN_PROMPT",
    "GEMINI_QUICK_SCREEN_PROMPT",
    "ollama_reachable",
    "resolved_vision_backend",
    "get_gemini_client",
    "ollama_describe",
    "gemini_describe_raw",
    "gemini_describe",
]


# ── Config ──────────────────────────────────────────────────────────

# Vision backend selection. "auto" picks ollama if reachable, else gemini.
VISION_BACKEND: str       = os.environ.get("JARVIS_VISION_BACKEND", "auto")
OLLAMA_VISION_MODEL: str  = os.environ.get("JARVIS_OLLAMA_VISION_MODEL", "qwen2.5vl:3b")
OLLAMA_URL: str           = os.environ.get("JARVIS_OLLAMA_URL", "http://localhost:11434")

# gemini-2.5-flash-lite — chosen for speed (verified 2026-04-28).
# Latency benchmark (one-shot screenshot describe, 70 KB JPEG, quick
# prompt):
#   gemini-2.5-flash-lite           ~11.5 s  ← chosen
#   gemini-2.5-flash                503 (overloaded that day)
#   gemini-3-flash-preview          ~20.1 s
#   gemini-3.1-flash-lite-preview   ~20.9 s  (was the default — too slow)
#   gemini-3.1-flash-live-preview   1011 quota (free-tier paid-only)
#   gemini-2.0-flash                429 (free-tier limit 0)
# 2.5-lite output quality is sufficient for "describe what the user
# sees" + "list UI elements with coordinates". Swap to gemini-2.5-flash
# (full) when it's not 503ing for higher accuracy on tricky UIs.
GEMINI_MODEL: str = "gemini-2.5-flash-lite"

# Default prompt for computer-use's perception loop. Asks for UI
# elements + coordinates so the action loop can pick a click target.
GEMINI_SCREEN_PROMPT: str = (
    "You are helping a voice assistant control a desktop computer. "
    "Describe the current screen state: what application is open, all "
    "visible UI elements (buttons, text fields, menus, links), and their "
    "approximate pixel coordinates (x, y from top-left corner). "
    "Be specific and concise — the assistant will decide what to click or type."
)

# Casual "what's on my screen" prompt used by the one-shot screenshot
# tool. No coordinates, no element list — just 1-2 sentences. Returns
# in 1-3s vs the detailed prompt's 10-15s.
GEMINI_QUICK_SCREEN_PROMPT: str = (
    "In one or two sentences, describe what's on this screen — what app "
    "is open, what the user appears to be doing. No coordinates, no "
    "element list. Speak naturally as if telling someone over the phone."
)


# ── Backend selection ──────────────────────────────────────────────

def ollama_reachable() -> bool:
    """Quick TCP probe — Ollama running on localhost?"""
    import socket
    try:
        host_port = OLLAMA_URL.replace("http://", "").replace("https://", "")
        host, port_s = host_port.split(":", 1)
        port = int(port_s.split("/")[0])
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except Exception:
        return False


def resolved_vision_backend() -> str:
    """Decide which backend to use this call. 'auto' picks ollama first."""
    if VISION_BACKEND == "ollama":
        return "ollama"
    if VISION_BACKEND == "gemini":
        return "gemini"
    return "ollama" if ollama_reachable() else "gemini"


# ── Gemini ─────────────────────────────────────────────────────────

def get_gemini_client():
    """Return a google.genai Client. Raise ComputerUseError if key missing.

    ComputerUseError is lazy-imported from `tools.computer_use` so this
    module doesn't pull computer_use at boot; by the time this function
    runs, both modules are fully loaded. Preserves the exception
    identity the test suite (and downstream callers) expect.
    """
    from google import genai
    from tools.computer_use import ComputerUseError
    key = os.environ.get("GOOGLE_API_KEY", "")
    if not key:
        raise ComputerUseError("GOOGLE_API_KEY not set in environment")
    return genai.Client(api_key=key)


async def gemini_describe_raw(
    image_bytes: bytes,
    mime_type: str = "image/png",
    prompt: str = GEMINI_SCREEN_PROMPT,
) -> str:
    """Direct Gemini call, no backend routing. Used when backend=gemini."""
    from google.genai import types as genai_types
    client = get_gemini_client()
    loop = asyncio.get_running_loop()

    def _call() -> str:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[
                genai_types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                prompt,
            ],
        )
        return response.text or "(no description returned)"

    return await loop.run_in_executor(None, _call)


# ── Ollama ─────────────────────────────────────────────────────────

async def ollama_describe(
    image_bytes: bytes,
    mime_type: str = "image/png",
    prompt: str = GEMINI_SCREEN_PROMPT,
) -> str:
    """Send image bytes to local Ollama vision model. Free, on-device."""
    import base64
    import json
    import urllib.request
    b64 = base64.b64encode(image_bytes).decode("ascii")
    payload = json.dumps({
        "model": OLLAMA_VISION_MODEL,
        "prompt": prompt,
        "images": [b64],
        "stream": False,
        "options": {"temperature": 0.2},
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    loop = asyncio.get_running_loop()

    def _call() -> str:
        # 180s timeout — first call loads ~3-5GB of model weights to GPU
        # which can take 30-90s. Subsequent calls are 1-5s once warm.
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = json.loads(resp.read())
        text = (data.get("response") or "").strip()
        return text or "(no description returned)"

    return await loop.run_in_executor(None, _call)


# ── Dispatch ───────────────────────────────────────────────────────

async def gemini_describe(
    image_bytes: bytes,
    mime_type: str = "image/png",
    prompt: str = GEMINI_SCREEN_PROMPT,
) -> str:
    """Describe an image — routes to Ollama (local) or Gemini (cloud).

    Backend chosen by JARVIS_VISION_BACKEND env (auto/ollama/gemini).
    Default is "auto": Ollama if reachable on localhost, else Gemini.
    Falls back to Gemini if Ollama call fails for any reason — so a
    misbehaving local model never breaks voice replies.
    """
    backend = resolved_vision_backend()
    if backend == "ollama":
        try:
            return await ollama_describe(image_bytes, mime_type, prompt)
        except Exception as e:
            logger.warning(f"[vision] ollama failed ({e}); falling back to gemini")
            return await gemini_describe_raw(image_bytes, mime_type, prompt)
    return await gemini_describe_raw(image_bytes, mime_type, prompt)
