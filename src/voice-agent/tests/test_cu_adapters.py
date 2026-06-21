"""Unit tests for the multi-provider computer-use adapters (SDKs mocked)."""
import asyncio
import base64
import json

_IMG = base64.b64encode(b"fake-png-bytes").decode()

from pipeline.cu_adapters.base import ToolCall, ToolResult, StepResult, strictify
from pipeline.cu_adapters import provider_for, available_providers


def test_strictify_sets_additional_properties_false():
    schema = {"type": "object", "properties": {"a": {"type": "object", "properties": {}}}}
    out = strictify(schema)
    assert out["additionalProperties"] is False
    assert out["properties"]["a"]["additionalProperties"] is False


def test_dataclasses_shape():
    c = ToolCall(id="t1", action="click", args={"element": 3})
    r = ToolResult(call_id="t1", text="ok", image_b64="abc")
    s = StepResult(text="hi", calls=[c])
    assert s.calls[0].action == "click" and r.call_id == "t1"


def test_provider_for_routing():
    assert provider_for("claude-sonnet-4-6") == "anthropic"
    assert provider_for("gpt-5.5") == "openai"
    assert provider_for("gemini-3-flash-preview") == "gemini"
    assert provider_for("") == "anthropic"


def test_available_providers_keys():
    av = available_providers()
    assert set(av.keys()) == {"anthropic", "openai", "gemini"}


def test_anthropic_adapter_parses_tool_use():
    from pipeline.cu_adapters.anthropic_adapter import AnthropicCUAdapter

    class _Block:
        def __init__(self, **k):
            self.__dict__.update(k)

    class _Resp:
        content = [
            _Block(type="text", text="clicking"),
            _Block(type="tool_use", id="t1", name="computer_use", input={"action": "click", "element": 3}),
        ]

    class _Msgs:
        def create(self, **k):
            return _Resp()

    class _Client:
        messages = _Msgs()

    a = AnthropicCUAdapter("claude-sonnet-4-6", "sys", client=_Client())
    a.seed("do it", "imgb64")
    res = asyncio.run(a.next_step())
    assert res.text == "clicking"
    assert res.calls[0].action == "click" and res.calls[0].args["element"] == 3
    a.add_results([ToolResult("t1", json.dumps({"ok": True}), "img2")])
    assert a.messages[-1]["role"] == "user"
    # export_history drops images
    hist = a.export_history()
    assert not _has_image(hist)


def _has_image(messages):
    flat = json.dumps(messages)
    return '"type": "image"' in flat


def test_openai_adapter_parses_tool_calls():
    from pipeline.cu_adapters.openai_adapter import OpenAICUAdapter

    class _Fn:
        name = "computer_use"
        arguments = json.dumps({"action": "type", "text": "hi"})

    class _TC:
        id = "c1"
        type = "function"
        function = _Fn()

    class _Msg:
        content = "typing"
        tool_calls = [_TC()]

    class _Choice:
        message = _Msg()

    class _Resp:
        choices = [_Choice()]

    class _Comp:
        def create(self, **k):
            return _Resp()

    class _Client:
        chat = type("C", (), {"completions": _Comp()})()

    a = OpenAICUAdapter("gpt-5.5", "sys", client=_Client())
    a.seed("do it", _IMG)
    res = asyncio.run(a.next_step())
    assert res.calls[0].action == "type" and res.calls[0].args["text"] == "hi"
    a.add_results([ToolResult("c1", "{}", _IMG)])
    assert any(m["role"] == "tool" for m in a.messages)
    assert a.messages[-1]["role"] == "user"  # screenshot follows as a user image


def test_gemini_adapter_parses_function_calls():
    from pipeline.cu_adapters.gemini_adapter import GeminiCUAdapter

    class _FC:
        name = "computer_use"
        args = {"action": "scroll", "element": 2, "direction": "down"}

    class _Part:
        text = None
        function_call = _FC()

    class _Content:
        role = "model"
        parts = [_Part()]

    class _Cand:
        content = _Content()

    class _Resp:
        candidates = [_Cand()]

    class _Models:
        def generate_content(self, **k):
            return _Resp()

    class _Client:
        models = _Models()

    a = GeminiCUAdapter("gemini-3-flash-preview", "sys", client=_Client())
    a.seed("do it", _IMG)
    res = asyncio.run(a.next_step())
    assert res.calls[0].action == "scroll" and res.calls[0].args["element"] == 2
    a.add_results([ToolResult("computer_use", "{}", _IMG)])
    assert len(a.contents) >= 2
