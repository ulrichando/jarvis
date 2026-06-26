"""Describe ImageContent in the supervisor's chat_ctx out-of-band, so a
TEXT-ONLY brain (DeepSeek) can 'see' images instead of choking on them.

The supervisor can't call a vision tool for an image it can't perceive — so the
system describes images FOR it, transparently: when the route model is text-only
(computer_use_vision.decide_mode == "text"), each ImageContent in THIS
generation's (ephemeral) ctx is replaced with a Gemini-produced text
description. Cached per image — describe once, not every turn (the wedge it
fixes was the SAME image re-sent every turn). Best-effort: any failure leaves
the ImageContent for sanitizers/image_content_strip.py to replace with a
placeholder, so a failed look never bricks the turn or becomes a confabulated
description.

Pairs with vision/gemini_vision.py (the actual vision call) and
sanitizers/image_content_strip.py (the never-brick floor).
"""
from __future__ import annotations

import asyncio
import hashlib
import logging

logger = logging.getLogger("jarvis.image_describe")

_CACHE: dict[str, str] = {}       # image content hash -> description
_CACHE_MAX = 256
_DEFAULT_TIMEOUT_S = 25.0          # Gemini 3 Pro thinks ~5s; generous headroom


def _hash(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8", "ignore")).hexdigest()


async def describe_ctx_images(chat_ctx, *, timeout_s: float = _DEFAULT_TIMEOUT_S) -> int:
    """Replace each base64 ImageContent in chat_ctx with a text description.
    Returns the count described. Mutates the (ephemeral, per-generation)
    chat_ctx in place. No-op + 0 when no vision backend / no images."""
    try:
        from livekit.agents.llm import ImageContent
        from vision import gemini_vision
    except Exception:
        return 0
    if not gemini_vision.gemini_vision_available():
        return 0

    described = 0
    for item in getattr(chat_ctx, "items", []) or []:
        content = getattr(item, "content", None)
        if not isinstance(content, list) or not content:
            continue
        changed = False
        new_content = []
        for part in content:
            url = getattr(part, "image", None) if isinstance(part, ImageContent) else None
            if isinstance(url, str) and url.startswith("data:"):
                desc = await _describe_one(url, timeout_s, gemini_vision)
                if desc:
                    new_content.append(f"[image — {desc}]")
                    described += 1
                    changed = True
                    continue
            new_content.append(part)  # not a data: image, or describe failed → leave it
        if changed:
            item.content = new_content
    return described


async def _describe_one(data_url: str, timeout_s: float, gemini_vision) -> str | None:
    key = _hash(data_url)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    try:
        desc = await asyncio.wait_for(
            asyncio.to_thread(gemini_vision.describe_data_url, data_url), timeout_s
        )
    except Exception as e:  # noqa: BLE001 — best-effort; the strip net catches the image
        logger.warning("[vision] image describe failed (%s) — leaving for strip net", e)
        return None
    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.clear()
    _CACHE[key] = desc
    logger.info("[vision] described a ctx image via Gemini (%d chars)", len(desc))
    return desc
