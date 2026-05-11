"""Vision-backend primitives: Kimi vision + Ollama dispatch.

Low-level image-describe pipeline shared by every "what's on the
screen?" feature (computer_use perception loop, screenshot tool,
live_screen, webcam_capture, face_id cross-checks).

Backend selection:
  * `JARVIS_VISION_BACKEND=auto` (default) — use Ollama if reachable
    on localhost, else Kimi.
  * `JARVIS_VISION_BACKEND=ollama` — force on-device (free, private,
    slower ~5-15 s/frame on RTX 2060 6GB).
  * `JARVIS_VISION_BACKEND=kimi` — force cloud (Moonshot's
    moonshot-v1-8k-vision-preview; requires KIMI_API_KEY).

Auto-fallback: when Ollama is configured/reachable but the call
fails for any reason, this module silently falls back to Kimi so
a misbehaving local model can't break voice replies.

Why Kimi (and not Gemini): Gemini's Generative Language API requires
the GCP project to have the API enabled — a foot-gun on accounts that
never opened the cloud console. Live failure 2026-05-11: GOOGLE_API_KEY
was valid but Gemini returned "API is disabled" 403s, leaving JARVIS
silent on every "what's on my screen?". Kimi's REST endpoint is
OpenAI-compatible and has no such project-level gate. GOOGLE_API_KEY
is kept in env for the geolocation flow (Wi-Fi positioning) only.

Hoisted from `tools/computer_use.py` 2026-05-10 (Step 7 of the audit),
swapped Gemini → Kimi 2026-05-11.
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
    "KIMI_VISION_MODEL",
    "KIMI_BASE_URL",
    "VISION_SCREEN_PROMPT",
    "VISION_QUICK_SCREEN_PROMPT",
    "ollama_reachable",
    "resolved_vision_backend",
    "get_kimi_client",
    "ollama_describe",
    "kimi_describe_raw",
    "vision_describe",
]


# ── Config ──────────────────────────────────────────────────────────

# Vision backend selection. "auto" picks ollama if reachable, else kimi.
VISION_BACKEND: str       = os.environ.get("JARVIS_VISION_BACKEND", "auto")
OLLAMA_VISION_MODEL: str  = os.environ.get("JARVIS_OLLAMA_VISION_MODEL", "qwen2.5vl:3b")
OLLAMA_URL: str           = os.environ.get("JARVIS_OLLAMA_URL", "http://localhost:11434")

# Moonshot's smallest vision model — 8k context is plenty for one
# screenshot + a short prompt. `moonshot-v1-8k-vision-preview` is the
# cheapest stable option (32k and 128k variants exist if a future
# caller needs more room). Default endpoint is the global `.ai` host;
# the `.cn` host is mainland-China-only with a separate key namespace.
KIMI_VISION_MODEL: str    = os.environ.get("JARVIS_KIMI_VISION_MODEL", "moonshot-v1-8k-vision-preview")
KIMI_BASE_URL: str        = os.environ.get("JARVIS_KIMI_BASE_URL", "https://api.moonshot.ai/v1")

# Default prompt for computer-use's perception loop. Asks for UI
# elements + coordinates so the action loop can pick a click target.
VISION_SCREEN_PROMPT: str = (
    "You are helping a voice assistant control a desktop computer. "
    "Describe the current screen state: what application is open, all "
    "visible UI elements (buttons, text fields, menus, links), and their "
    "approximate pixel coordinates (x, y from top-left corner). "
    "Be specific and concise — the assistant will decide what to click or type."
)

# Casual "what's on my screen" prompt used by the one-shot screenshot
# tool. No coordinates, no element list — just 1-2 sentences.
VISION_QUICK_SCREEN_PROMPT: str = (
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
    if VISION_BACKEND == "kimi":
        return "kimi"
    return "ollama" if ollama_reachable() else "kimi"


# ── Kimi (Moonshot, OpenAI-compatible) ────────────────────────────

def get_kimi_client():
    """Return an OpenAI client pointed at Moonshot's `.ai` endpoint.

    The official path is to use openai-python with a swapped `base_url`
    — Moonshot doesn't ship a first-party SDK and their API is
    response-shape-compatible with OpenAI's chat-completions.

    ComputerUseError is lazy-imported from `tools.computer_use` so this
    module doesn't pull computer_use at boot; by the time this function
    runs, both modules are fully loaded. Preserves the exception
    identity the test suite (and downstream callers) expect.
    """
    from openai import OpenAI
    from tools.computer_use import ComputerUseError
    key = os.environ.get("KIMI_API_KEY", "")
    if not key:
        raise ComputerUseError("KIMI_API_KEY not set in environment")
    return OpenAI(api_key=key, base_url=KIMI_BASE_URL)


async def kimi_describe_raw(
    image_bytes: bytes,
    mime_type: str = "image/png",
    prompt: str = VISION_SCREEN_PROMPT,
) -> str:
    """Direct Kimi call, no backend routing. Used when backend=kimi."""
    import base64
    client = get_kimi_client()
    b64 = base64.b64encode(image_bytes).decode("ascii")
    data_url = f"data:{mime_type};base64,{b64}"
    loop = asyncio.get_running_loop()

    def _call() -> str:
        # OpenAI-compatible vision message shape; same body Moonshot's
        # docs publish at platform.kimi.ai/docs/guide/use-kimi-vision-model.
        resp = client.chat.completions.create(
            model=KIMI_VISION_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": "You are Kimi. Answer concisely.",
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": data_url}},
                        {"type": "text", "text": prompt},
                    ],
                },
            ],
            # Cap output so a chatty model can't bloat the spoken reply.
            max_tokens=600,
            temperature=0.2,
        )
        text = resp.choices[0].message.content if resp.choices else ""
        return (text or "").strip() or "(no description returned)"

    return await loop.run_in_executor(None, _call)


# ── Ollama ─────────────────────────────────────────────────────────

async def ollama_describe(
    image_bytes: bytes,
    mime_type: str = "image/png",
    prompt: str = VISION_SCREEN_PROMPT,
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

async def vision_describe(
    image_bytes: bytes,
    mime_type: str = "image/png",
    prompt: str = VISION_SCREEN_PROMPT,
) -> str:
    """Describe an image — routes to Ollama (local) or Kimi (cloud).

    Backend chosen by JARVIS_VISION_BACKEND env (auto/ollama/kimi).
    Default is "auto": Ollama if reachable on localhost, else Kimi.
    Falls back to Kimi if Ollama call fails for any reason — so a
    misbehaving local model never breaks voice replies.
    """
    backend = resolved_vision_backend()
    if backend == "ollama":
        try:
            return await ollama_describe(image_bytes, mime_type, prompt)
        except Exception as e:
            logger.warning(f"[vision] ollama failed ({e}); falling back to kimi")
            return await kimi_describe_raw(image_bytes, mime_type, prompt)
    return await kimi_describe_raw(image_bytes, mime_type, prompt)
