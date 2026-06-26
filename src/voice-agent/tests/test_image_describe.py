"""image_describe + gemini_vision.describe_data_url — the vision TOOL that lets
a TEXT-ONLY supervisor 'see' ctx images (cached, best-effort). The Gemini network
call is stubbed; the live call is verified out-of-band.
"""
from __future__ import annotations

import asyncio
import base64
import sys
from pathlib import Path

import pytest

_VOICE_AGENT_ROOT = Path(__file__).resolve().parent.parent
if str(_VOICE_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_VOICE_AGENT_ROOT))

from pipeline import image_describe as imd
from vision import gemini_vision


class _Item:
    def __init__(self, content):
        self.content = content


class _Ctx:
    def __init__(self, items):
        self.items = items


def _img(url="data:image/png;base64,AAAA"):
    from livekit.agents.llm import ImageContent
    return ImageContent(image=url)


def _has_image(content) -> bool:
    from livekit.agents.llm import ImageContent
    return any(isinstance(p, ImageContent) for p in content)


# ── gemini_vision.describe_data_url: data-URL parsing ──────────────────


def test_describe_data_url_parses_mime_and_b64(monkeypatch):
    seen = {}

    def fake(jpeg, q=None, *, mime_type="image/jpeg"):
        seen["mime"], seen["bytes"] = mime_type, jpeg
        return "a cat"

    monkeypatch.setattr(gemini_vision, "describe_jpeg", fake)
    url = "data:image/png;base64," + base64.b64encode(b"PNGDATA").decode()
    assert gemini_vision.describe_data_url(url) == "a cat"
    assert seen["mime"] == "image/png"
    assert seen["bytes"] == b"PNGDATA"


def test_describe_data_url_rejects_non_data_url():
    with pytest.raises(ValueError):
        gemini_vision.describe_data_url("https://example.com/y.png")


# ── image_describe.describe_ctx_images: the pre-pass ───────────────────


def test_ctx_image_replaced_with_description(monkeypatch):
    imd._CACHE.clear()
    monkeypatch.setattr(gemini_vision, "gemini_vision_available", lambda: True)
    monkeypatch.setattr(gemini_vision, "describe_data_url", lambda url, q=None: "a red square")
    ctx = _Ctx([_Item([{"type": "text", "text": "what is this"}, _img()])])
    n = asyncio.run(imd.describe_ctx_images(ctx))
    assert n == 1
    assert not _has_image(ctx.items[0].content)             # image gone
    assert any(isinstance(p, str) and "a red square" in p   # description in its place
               for p in ctx.items[0].content)


def test_description_is_cached_per_image(monkeypatch):
    imd._CACHE.clear()
    calls = []
    monkeypatch.setattr(gemini_vision, "gemini_vision_available", lambda: True)
    monkeypatch.setattr(gemini_vision, "describe_data_url",
                        lambda url, q=None: calls.append(url) or "desc")
    same = "data:image/png;base64,SAME"
    asyncio.run(imd.describe_ctx_images(_Ctx([_Item([_img(same)])])))
    asyncio.run(imd.describe_ctx_images(_Ctx([_Item([_img(same)])])))
    assert len(calls) == 1                                  # second hit the cache


def test_no_vision_backend_is_noop(monkeypatch):
    monkeypatch.setattr(gemini_vision, "gemini_vision_available", lambda: False)
    ctx = _Ctx([_Item([_img()])])
    assert asyncio.run(imd.describe_ctx_images(ctx)) == 0
    assert _has_image(ctx.items[0].content)                 # untouched


def test_describe_failure_leaves_image_for_strip(monkeypatch):
    imd._CACHE.clear()
    monkeypatch.setattr(gemini_vision, "gemini_vision_available", lambda: True)

    def boom(url, q=None):
        raise RuntimeError("vision down")

    monkeypatch.setattr(gemini_vision, "describe_data_url", boom)
    ctx = _Ctx([_Item([_img()])])
    assert asyncio.run(imd.describe_ctx_images(ctx)) == 0
    assert _has_image(ctx.items[0].content)                 # left for image_content_strip
