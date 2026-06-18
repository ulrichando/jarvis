"""Local Ollama vision fallback — image + question → short text answer.

The offline last resort for vision: when a cloud vision call (Anthropic)
is unavailable (no key, network down, provider error), JARVIS can still
"see" by routing the frame to a LOCAL Ollama vision model. Talks to the
Ollama OpenAI-compatible endpoint (``JARVIS_OLLAMA_URL`` → ``/v1``) with
the configured model (``JARVIS_OLLAMA_VISION_MODEL`` — e.g. ``moondream``
on a small box, ``llava`` / ``qwen2.5-vl`` on a big one).

Gated by callers via ``JARVIS_LOCAL_VISION_ENABLED`` (see
``ollama_vision_available``); this module just performs the call. Part of
the local offline fallback stack — see ``pipeline/config.py``'s
"Local offline fallback stack" block and the 2026-06-15 local-LLM design.
"""
from __future__ import annotations

import base64
import logging
import os

logger = logging.getLogger("jarvis.vision.ollama")


def ollama_vision_available() -> bool:
    """True when the local vision fallback is switched on.

    Reachability is checked lazily at call time, not here — a network
    probe in a tool-gate path would add latency to every turn. The
    fallback either answers or raises a clear error when actually used.
    """
    return os.environ.get("JARVIS_LOCAL_VISION_ENABLED", "0") == "1"


def _base_url() -> str:
    """OpenAI-compatible base URL for Ollama.

    ``JARVIS_OLLAMA_URL`` is the native root (``…:11434``); the
    OpenAI-compat surface lives at ``…/v1``. Accept either form in the
    env and normalize to ``/v1`` so operators can't get it subtly wrong.
    """
    raw = os.environ.get("JARVIS_OLLAMA_URL", "http://127.0.0.1:11434").strip().rstrip("/")
    if not raw:
        raw = "http://127.0.0.1:11434"
    return raw if raw.endswith("/v1") else raw + "/v1"


def _model() -> str:
    return os.environ.get("JARVIS_OLLAMA_VISION_MODEL", "llava").strip() or "llava"


def model_label() -> str:
    """Telemetry/payload label for the backend that answered."""
    return f"ollama:{_model()}"


def _timeout_s() -> float:
    try:
        return float(os.environ.get("JARVIS_LOCAL_VISION_TIMEOUT_S", "60"))
    except (TypeError, ValueError):
        return 60.0


def analyze_jpeg(jpeg: bytes, question: str, *, system: str | None = None) -> str:
    """One local Ollama vision call: JPEG + question → short text answer.

    Uses ``/v1/chat/completions`` with an ``image_url`` data URI — the
    format Ollama vision models accept over the OpenAI-compat surface.
    Raises on transport error or empty output so the caller can surface a
    clear tool error (and, for the supervisor's confab gate, so a failed
    "I looked" never becomes a fabricated description).
    """
    from openai import OpenAI  # lazy — keeps import cost off the hot path

    client = OpenAI(base_url=_base_url(), api_key="ollama", timeout=_timeout_s())
    data_uri = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")
    messages: list[dict] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append(
        {
            "role": "user",
            "content": [
                {"type": "text", "text": question},
                {"type": "image_url", "image_url": {"url": data_uri}},
            ],
        }
    )
    resp = client.chat.completions.create(
        model=_model(),
        messages=messages,
        max_tokens=300,
        temperature=0.2,
    )
    answer = (resp.choices[0].message.content or "").strip()
    if not answer:
        raise RuntimeError("local vision model returned no text")
    return answer
