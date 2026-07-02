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


# ── computer_use_service._trim_history (plan 001: orphan tool_result guard) ────

def _anthropic_history(steps):
    """Simulate an Anthropic adapter message list after `steps` tool round-trips:
    a seed user turn, then per step assistant(tool_use) + user(tool_result)."""
    msgs = [{"role": "user", "content": [{"type": "text", "text": "do the thing"}]}]
    for i in range(steps):
        msgs.append({"role": "assistant", "content": [
            {"type": "tool_use", "id": f"t{i}", "name": "computer_use",
             "input": {"action": "capture"}}]})
        msgs.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": f"t{i}",
             "content": [{"type": "text", "text": "ok"}]}]})
    return msgs


def test_trim_never_starts_with_orphan_tool_result():
    # 30 steps -> 61 messages, well over _MAX_HISTORY (40); forces a trim. The
    # head must be a user turn that does NOT carry a tool_result block (that would
    # 400 on Anthropic: "unexpected tool_result").
    from computer_use_service import _trim_history, _MAX_HISTORY
    trimmed = _trim_history(_anthropic_history(30))
    assert trimmed, "trim must not empty the history"
    head = trimmed[0]
    assert head.get("role") == "user"
    content = head.get("content") or []
    assert not any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content)
    assert len(trimmed) <= _MAX_HISTORY


def test_trim_short_history_untouched():
    from computer_use_service import _trim_history
    msgs = _anthropic_history(3)  # 7 messages, under the cap
    assert _trim_history(msgs) is msgs


# ── AnthropicCUAdapter caching / effort / image-cap (plan 002) ────────────────

def _capturing_anthropic(model, system="sys prompt"):
    """Build an AnthropicCUAdapter whose mock client records create() kwargs."""
    from pipeline.cu_adapters.anthropic_adapter import AnthropicCUAdapter
    captured = {}

    class _Resp:
        content = []

    class _Msgs:
        def create(self, **k):
            captured.clear()
            captured.update(k)
            return _Resp()

    class _Client:
        messages = _Msgs()

    return AnthropicCUAdapter(model, system, client=_Client()), captured


def test_anthropic_adapter_caches_prefix_and_sets_effort():
    a, captured = _capturing_anthropic("claude-sonnet-4-6")
    a.seed("do it", "imgb64")
    asyncio.run(a.next_step())
    sysb = captured["system"]
    assert isinstance(sysb, list) and sysb[-1]["cache_control"] == {"type": "ephemeral"}
    assert captured["tools"][0]["cache_control"] == {"type": "ephemeral"}
    assert captured["output_config"] == {"effort": "medium"}


def test_anthropic_adapter_effort_by_model(monkeypatch):
    def _effort(model):
        a, captured = _capturing_anthropic(model)
        a.seed("x", None)
        asyncio.run(a.next_step())
        return captured.get("output_config")
    assert _effort("claude-opus-4-7") == {"effort": "high"}
    assert _effort("claude-opus-4-8") == {"effort": "high"}
    assert _effort("claude-sonnet-4-6") == {"effort": "medium"}
    assert _effort("claude-haiku-4-5") is None       # unknown/haiku → API default
    monkeypatch.setenv("JARVIS_CU_EFFORT_DISABLED", "1")
    assert _effort("claude-sonnet-4-6") is None       # kill-switch


def test_anthropic_adapter_caps_inrun_images():
    a, _ = _capturing_anthropic("claude-sonnet-4-6")
    a.seed("task", "seedimg")                          # 1 image-bearing user turn
    for i in range(5):
        a.add_results([ToolResult(f"t{i}", "{}", f"img{i}")])  # +5 image turns

    def _has_img(m):
        c = m.get("content")
        if not isinstance(c, list):
            return False
        for b in c:
            if isinstance(b, dict) and b.get("type") == "image":
                return True
            if isinstance(b, dict) and b.get("type") == "tool_result" and isinstance(b.get("content"), list):
                if any(isinstance(x, dict) and x.get("type") == "image" for x in b["content"]):
                    return True
        return False

    img_turns = [m for m in a.messages if m.get("role") == "user" and _has_img(m)]
    assert len(img_turns) <= 3, "should keep at most the last 3 screenshots in-run"
    # Pairing intact: every tool_result envelope still has non-empty content.
    for m in a.messages:
        if m.get("role") == "user" and isinstance(m.get("content"), list):
            for b in m["content"]:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    assert isinstance(b.get("content"), list) and b["content"]
