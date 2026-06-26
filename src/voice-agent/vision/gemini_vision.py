"""Out-of-band image description via Google Gemini — the vision TOOL for a
TEXT-ONLY conversational brain.

JARVIS's supervisor runs on a text-only model (DeepSeek) by default: it can't
read an `image_url` block — it 400s on it (see sanitizers/image_content_strip).
This module gives it eyes WITHOUT changing the brain: one Gemini multimodal
call turns an image into a concrete text description the text-only supervisor
can then reason and talk about. Mirrors vision/ollama_vision.analyze_jpeg —
synchronous (run it via asyncio.to_thread off the hot path), and RAISES on
failure rather than fabricating a description (the caller falls back to the
strip placeholder, so a failed look never becomes a confabulated one).

Model: gemini-3.1-pro-preview (best current Gemini vision), overridable via
JARVIS_VISION_GEMINI_MODEL. Uses the GOOGLE_API_KEY JARVIS already holds.
"""
from __future__ import annotations

import base64
import logging
import os

logger = logging.getLogger("jarvis.vision.gemini")

_DEFAULT_MODEL = "gemini-3.1-pro-preview"
_DESCRIBE_PROMPT = (
    "Describe this image for someone who cannot see it. Be concise and concrete: "
    "what it shows, any visible text (read it verbatim), key objects or people, "
    "and the overall context. 2-4 sentences, no preamble."
)


def gemini_vision_available() -> bool:
    """A vision backend is reachable iff the Google key is set."""
    return bool(os.environ.get("GOOGLE_API_KEY", "").strip())


def _model() -> str:
    return os.environ.get("JARVIS_VISION_GEMINI_MODEL", _DEFAULT_MODEL).strip() or _DEFAULT_MODEL


def describe_jpeg(image_bytes: bytes, question: str | None = None,
                  *, mime_type: str = "image/jpeg") -> str:
    """One Gemini vision call: image bytes + question → text. Raises on
    transport error / empty output. Blocking — call via asyncio.to_thread."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
    resp = client.models.generate_content(
        model=_model(),
        contents=[
            types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
            question or _DESCRIBE_PROMPT,
        ],
        # Gemini 3.x Pro *requires* thinking mode (thinking_budget=0 → 400
        # "this model only works in thinking mode"), and thinking spends ~400-500
        # output tokens BEFORE the answer — at max_output_tokens=400 the reply got
        # MAX_TOKENS-truncated mid-sentence. 2048 leaves room for thinking + a
        # 2-4 sentence description. (Override the model to a Flash for lower
        # latency/cost via JARVIS_VISION_GEMINI_MODEL.)
        config=types.GenerateContentConfig(max_output_tokens=2048, temperature=0.2),
    )
    answer = (getattr(resp, "text", None) or "").strip()
    if not answer:
        raise RuntimeError("gemini vision returned no text")
    return answer


def describe_data_url(data_url: str, question: str | None = None) -> str:
    """Describe an image given as a `data:<mime>;base64,<...>` URL — the shape
    livekit ImageContent carries. Raises ValueError on a non-data URL."""
    if not data_url.startswith("data:") or "," not in data_url:
        raise ValueError("not a base64 data: URL")
    header, b64 = data_url.split(",", 1)
    mime = "image/jpeg"
    inner = header[len("data:"):]
    if inner:
        mime = inner.split(";", 1)[0] or "image/jpeg"
    return describe_jpeg(base64.b64decode(b64), question, mime_type=mime)
