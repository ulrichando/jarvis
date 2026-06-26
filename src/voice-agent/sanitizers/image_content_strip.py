"""Strip image content blocks from OpenAI-format messages (text-only providers).

JARVIS's conversational speech models — DeepSeek / Groq / Kimi / local llama —
are TEXT-ONLY. By design vision is handled OUT-OF-BAND (webcam / computer_use
grab a frame and make a separate Anthropic vision call; tool results are
str-coerced), so the conversational rungs never need images. But an `image_url`
content block can still land in the chat_ctx — a user-shared image, a multimodal
client frame, a recalled conversations-DB row — and the OpenAI-compatible
text-only providers reject it with a NON-RETRYABLE 400:

    400 ... Failed to deserialize the JSON body: messages[N]:
    unknown variant `image_url`, expected `text`

Because the error is non-retryable, the FallbackAdapter does NOT recover: the
LLM inference task dies and the supervisor produces no speech. Worse, the image
stays in history, so EVERY subsequent turn re-sends it and 400s again — JARVIS
acks a request ("let me look at that") then never delivers the result, for the
rest of the session. (2026-06-26 "says he's doing it but never follows up"
incident; one live session bricked 8 turns in a row on deepseek-v4-flash.)

Fix: at the OpenAI-format serialization chokepoint (`to_chat_ctx`, shared by
every OpenAI-compatible provider) replace each image content part with a short
text placeholder. Anthropic uses its OWN serializer (not this one), so its
vision path is untouched — the strip is provider-correct by construction.
Mirrors sanitizers/deepseek_roundtrip.py's to_chat_ctx wrap and the
providers/anthropic_cached_llm.py `_strip_empty_text_blocks` placeholder
approach (drop the offending part, keep a text stand-in so message
alternation + tool_call/tool_result pairing stay intact). Idempotent.

ponytail: targets the image content shapes (`image_url`/`image`) that actually
brick — not every non-text type. A new non-text type that 400s is a new
signature to add to _IMAGE_TYPES, not a reason to over-strip today.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("jarvis.sanitizers.image_content_strip")

# OpenAI content-part shapes that carry an image. Text-only providers' content
# union only accepts `text`, so these 400 the whole request non-retryably.
_IMAGE_TYPES = frozenset({"image_url", "image"})
_PLACEHOLDER = "[image omitted — this model is text-only; vision is handled out-of-band]"


def _strip_image_parts(messages: list) -> int:
    """Replace image content parts with a text placeholder, in place. Returns
    the number of parts replaced (for logging). String content is left as-is
    (no parts to inspect); a message that was image-only keeps a single text
    placeholder so it never becomes empty (which would then trip the
    empty-content guards)."""
    stripped = 0
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        changed = False
        new_parts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") in _IMAGE_TYPES:
                new_parts.append({"type": "text", "text": _PLACEHOLDER})
                stripped += 1
                changed = True
            else:
                new_parts.append(part)
        if changed:
            msg["content"] = new_parts
    return stripped


def install() -> None:
    """Wrap openai.to_chat_ctx to strip image parts for text-only providers.
    Idempotent; coexists with (stacks on) deepseek_roundtrip's wrap of the
    same function — both capture the then-current to_chat_ctx and chain."""
    from livekit.agents.llm._provider_format import openai as oai_fmt

    if getattr(oai_fmt, "_jarvis_image_strip_patched", False):
        return
    orig = oai_fmt.to_chat_ctx

    def patched(*args, **kwargs):
        messages, extra = orig(*args, **kwargs)
        try:
            n = _strip_image_parts(messages)
            if n:
                logger.info(
                    "stripped %d image content part(s) — target model is text-only", n
                )
        except Exception:  # a sanitizer must never break the request path
            logger.exception("image_content_strip failed (non-fatal, passing through)")
        return messages, extra

    oai_fmt.to_chat_ctx = patched
    oai_fmt._jarvis_image_strip_patched = True
