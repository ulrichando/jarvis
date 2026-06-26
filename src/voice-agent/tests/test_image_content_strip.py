"""image_content_strip — strip image parts so a TEXT-ONLY conversational model
never 400s non-retryably on an `image_url` block (which bricks every subsequent
turn: JARVIS acks then never returns the result). The model call is not made —
these pin the pure transform + the to_chat_ctx wrap.
"""
from __future__ import annotations

import sys
from pathlib import Path

_VOICE_AGENT_ROOT = Path(__file__).resolve().parent.parent
if str(_VOICE_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_VOICE_AGENT_ROOT))

from sanitizers import image_content_strip as ics


def test_strips_image_url_keeps_text():
    msgs = [{"role": "user", "content": [
        {"type": "text", "text": "what's this?"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
    ]}]
    assert ics._strip_image_parts(msgs) == 1
    assert msgs[0]["content"] == [
        {"type": "text", "text": "what's this?"},
        {"type": "text", "text": ics._PLACEHOLDER},
    ]


def test_image_only_message_keeps_a_placeholder_not_empty():
    msgs = [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "x"}}]}]
    assert ics._strip_image_parts(msgs) == 1
    assert msgs[0]["content"] == [{"type": "text", "text": ics._PLACEHOLDER}]


def test_image_type_variant_also_stripped():
    msgs = [{"role": "user", "content": [{"type": "image", "source": {}}]}]
    assert ics._strip_image_parts(msgs) == 1


def test_string_content_untouched():
    msgs = [{"role": "user", "content": "hello"}]
    assert ics._strip_image_parts(msgs) == 0
    assert msgs[0]["content"] == "hello"


def test_text_only_parts_untouched():
    msgs = [{"role": "assistant", "content": [{"type": "text", "text": "ok"}]}]
    assert ics._strip_image_parts(msgs) == 0


def test_multiple_images_across_messages_counted():
    msgs = [
        {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "a"}}]},
        {"role": "user", "content": [{"type": "text", "text": "and this"},
                                     {"type": "image_url", "image_url": {"url": "b"}}]},
    ]
    assert ics._strip_image_parts(msgs) == 2


def test_install_wraps_to_chat_ctx_and_strips(monkeypatch):
    from livekit.agents.llm._provider_format import openai as oai_fmt

    def fake_to_chat_ctx(chat_ctx, *, inject_dummy_user_message=True):
        return ([{"role": "user", "content": [
            {"type": "text", "text": "hi"},
            {"type": "image_url", "image_url": {"url": "data:..."}},
        ]}], {})

    monkeypatch.setattr(oai_fmt, "to_chat_ctx", fake_to_chat_ctx, raising=False)
    monkeypatch.setattr(oai_fmt, "_jarvis_image_strip_patched", False, raising=False)
    ics.install()
    msgs, _ = oai_fmt.to_chat_ctx(object())
    types = [p.get("type") for p in msgs[0]["content"]]
    assert "image_url" not in types
    assert types == ["text", "text"]
    assert msgs[0]["content"][1]["text"] == ics._PLACEHOLDER
    assert getattr(oai_fmt, "_jarvis_image_strip_patched") is True
