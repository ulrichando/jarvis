"""Vision-backend primitives: Gemini Flash + Kimi + Ollama dispatch.

Low-level image-describe pipeline shared by every "what's on the
screen?" feature (computer_use perception loop, screenshot tool,
live_screen, webcam_capture, face_id cross-checks).

Backend selection:
  * `JARVIS_VISION_BACKEND=auto` (default) — Ollama if reachable on
    localhost, else Gemini, with auto-fallback to Kimi if Gemini
    returns an API-disabled / quota / billing error.
  * `JARVIS_VISION_BACKEND=ollama` — force on-device (free, private,
    slower ~5-15 s/frame on RTX 2060 6GB).
  * `JARVIS_VISION_BACKEND=gemini` — force Google's Generative
    Language API; falls through to Kimi only on hard errors.
  * `JARVIS_VISION_BACKEND=kimi` — force Moonshot's
    moonshot-v1-8k-vision-preview. Used as fallback above; can be
    set explicitly when Gemini's GCP project gate trips.

Why Gemini-then-Kimi (re-swapped 2026-05-11 after live latency
complaint on Kimi-primary): Gemini Flash Lite is ~3-4× faster than
Kimi vision (≈ 3s vs ≈ 11s for a one-shot screenshot describe). The
previous Gemini → Kimi swap on 2026-05-11 morning was driven by a
GCP "API is disabled" 403 from a project that had never opened the
Generative Language API. Once the API is enabled, Gemini is the
faster path. Kimi is retained as an automatic fallback so the same
foot-gun (key valid but API disabled / quota exceeded) can never
silence "what's on my screen?" again.

Hoisted from `tools/computer_use.py` 2026-05-10 (Step 7 of the audit).
Gemini → Kimi swap 2026-05-11 morning. Gemini-primary restored
2026-05-11 evening with Kimi auto-fallback.
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
    "GEMINI_VISION_MODEL",
    "KIMI_VISION_MODEL",
    "KIMI_BASE_URL",
    "VISION_SCREEN_PROMPT",
    "VISION_QUICK_SCREEN_PROMPT",
    "ollama_reachable",
    "resolved_vision_backend",
    "get_gemini_client",
    "get_kimi_client",
    "ollama_describe",
    "gemini_describe_raw",
    "kimi_describe_raw",
    "vision_describe",
]


# ── Config ──────────────────────────────────────────────────────────

# Vision backend selection. "auto" picks ollama if reachable, else gemini.
VISION_BACKEND: str       = os.environ.get("JARVIS_VISION_BACKEND", "auto")
OLLAMA_VISION_MODEL: str  = os.environ.get("JARVIS_OLLAMA_VISION_MODEL", "qwen2.5vl:3b")
OLLAMA_URL: str           = os.environ.get("JARVIS_OLLAMA_URL", "http://localhost:11434")

# gemini-2.5-flash-lite — chosen for speed (verified 2026-04-28).
# Latency benchmark (one-shot screenshot describe, 70 KB JPEG, quick
# prompt):
#   gemini-2.5-flash-lite           ~3-4s   ← chosen (default)
#   gemini-2.5-flash                ~5-7s   (higher accuracy, slower)
#   moonshot-v1-8k-vision-preview   ~11s    (kimi fallback)
# 2.5-lite output quality is sufficient for "describe what the user
# sees" + "list UI elements with coordinates". Override via env if a
# downstream caller needs higher accuracy.
GEMINI_VISION_MODEL: str  = os.environ.get("JARVIS_GEMINI_VISION_MODEL", "gemini-2.5-flash-lite")

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


# Marker substrings that identify Gemini failures we want to auto-
# fall-through to Kimi on. Match against str(exc).lower(); these are
# the wire-level messages the google-genai SDK surfaces.
_GEMINI_FALLBACK_MARKERS: tuple[str, ...] = (
    "api is disabled",         # GCP project hasn't opened Generative Language API
    "api has not been used",   # variant of the same
    "permission denied",        # 403
    "429",                     # wire-level status code
    "quota",                   # quota / billing exceeded
    "rate limit",              # rate-limit hit
    "resource_exhausted",      # gRPC status for 429
    "prepayment credits",      # AI Studio depleted-credits message
    "billing",                 # paid-only model on free tier
    "1011",                    # WebSocket quota error on live-preview models
    "503",                     # service overloaded
    "504",                     # gateway timeout
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
    """Decide which backend to use this call. 'auto' picks ollama first,
    then gemini. 'kimi' is explicit-only or fallback-on-failure."""
    if VISION_BACKEND == "ollama":
        return "ollama"
    if VISION_BACKEND == "gemini":
        return "gemini"
    if VISION_BACKEND == "kimi":
        return "kimi"
    # auto: prefer local, else cloud-fast (gemini)
    return "ollama" if ollama_reachable() else "gemini"


# ── Gemini (Google Generative Language) ────────────────────────────

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
    prompt: str = VISION_SCREEN_PROMPT,
) -> str:
    """Direct Gemini call, no backend routing. Used when backend=gemini."""
    from google.genai import types as genai_types
    client = get_gemini_client()
    loop = asyncio.get_running_loop()

    def _call() -> str:
        response = client.models.generate_content(
            model=GEMINI_VISION_MODEL,
            contents=[
                genai_types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                prompt,
            ],
        )
        return (response.text or "").strip() or "(no description returned)"

    return await loop.run_in_executor(None, _call)


def _is_gemini_fallback_error(exc: BaseException) -> bool:
    """Return True if the Gemini error is one we want to silently retry
    on Kimi (instead of surfacing). Catches the 'API is disabled' /
    quota / 5xx family that Kimi will route around."""
    s = (str(exc) or "").lower()
    return any(m in s for m in _GEMINI_FALLBACK_MARKERS)


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
        # 8s timeout — Ollama in production is either warm (1-5s) or
        # unresponsive. The previous 180s cap let a hung qwen2.5vl
        # block the voice loop for THREE MINUTES while the user
        # waited for a screen-share answer (live failure 2026-05-11
        # 12:25-12:29 UTC). With this cap, a cold-start that exceeds
        # 8s falls through to Gemini cleanly — annoying for the first
        # request of the day, but never user-visible as silence.
        # If you genuinely want to wait for a slow cold-start, set
        # JARVIS_OLLAMA_TIMEOUT_S in the env (read at call time).
        timeout_s = float(os.environ.get("JARVIS_OLLAMA_TIMEOUT_S", "8"))
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
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
    """Describe an image — routes to Ollama (local), Gemini (cloud,
    fast), or Kimi (cloud, fallback).

    Backend chosen by JARVIS_VISION_BACKEND env (auto/ollama/gemini/kimi).
    Default is "auto": Ollama if reachable on localhost, else Gemini.

    Auto-fallback chain:
      - Ollama failure  → fall through to Gemini.
      - Gemini failure with a known marker (API-disabled, quota, 5xx)
                        → fall through to Kimi.
      - Other Gemini failures bubble up so they surface in logs.
      - Kimi failure bubbles up (it's the last resort).
    """
    backend = resolved_vision_backend()
    if backend == "ollama":
        try:
            return await ollama_describe(image_bytes, mime_type, prompt)
        except Exception as e:
            logger.warning(f"[vision] ollama failed ({e}); falling back to gemini")
            backend = "gemini"  # fall through

    if backend == "gemini":
        try:
            return await gemini_describe_raw(image_bytes, mime_type, prompt)
        except Exception as e:
            if _is_gemini_fallback_error(e):
                logger.warning(
                    f"[vision] gemini failed with known marker ({type(e).__name__}: {e}); "
                    f"falling back to kimi"
                )
                return await kimi_describe_raw(image_bytes, mime_type, prompt)
            raise

    # backend == "kimi" (explicit or unreachable-other)
    return await kimi_describe_raw(image_bytes, mime_type, prompt)
