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


# ── Native computer_20251124 adapter (2026-07-04) ─────────────────────────────

def _tiny_png_b64(w=640, h=400):
    """Minimal real PNG (IHDR parseable) — no PIL dependency."""
    import struct
    import zlib

    def chunk(typ, data):
        return (struct.pack(">I", len(data)) + typ + data
                + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\x00" * (w * 3) for _ in range(h))
    return base64.b64encode(
        b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
    ).decode()


def test_native_anthropic_cu_gate(monkeypatch):
    from pipeline.cu_adapters import native_anthropic_cu
    monkeypatch.delenv("JARVIS_CU_NATIVE_ANTHROPIC", raising=False)
    assert native_anthropic_cu("claude-sonnet-5") is True
    assert native_anthropic_cu("claude-opus-4-8") is True
    assert native_anthropic_cu("claude-sonnet-4-6") is True
    assert native_anthropic_cu("claude-haiku-4-5") is False   # only the 20250124 surface
    assert native_anthropic_cu("gpt-5.5") is False
    assert native_anthropic_cu("gemini-3-flash-preview") is False
    monkeypatch.setenv("JARVIS_CU_NATIVE_ANTHROPIC", "0")     # kill-switch → SOM everywhere
    assert native_anthropic_cu("claude-opus-4-8") is False


def test_make_adapter_native_routing(monkeypatch):
    from pipeline.cu_adapters import make_adapter
    from pipeline.cu_adapters.anthropic_adapter import (AnthropicCUAdapter,
                                                        AnthropicNativeCUAdapter)
    monkeypatch.delenv("JARVIS_CU_NATIVE_ANTHROPIC", raising=False)
    assert isinstance(make_adapter("claude-sonnet-5", "s"), AnthropicNativeCUAdapter)
    assert type(make_adapter("claude-haiku-4-5", "s")) is AnthropicCUAdapter
    monkeypatch.setenv("JARVIS_CU_NATIVE_ANTHROPIC", "0")
    assert type(make_adapter("claude-sonnet-5", "s")) is AnthropicCUAdapter


def _capturing_native(model, system="sys prompt"):
    from pipeline.cu_adapters.anthropic_adapter import AnthropicNativeCUAdapter
    captured = {}

    class _Block:
        def __init__(self, **k):
            self.__dict__.update(k)

    class _Resp:
        content = []

    class _Msgs:
        def __init__(self):
            self.resp = _Resp()

        def create(self, **k):
            captured.clear()
            captured.update(k)
            return self.resp

    class _Client:
        def __init__(self):
            self.messages = _Msgs()

    client = _Client()
    return AnthropicNativeCUAdapter(model, system, client=client), captured, client, _Block


def test_native_adapter_declares_computer_20251124():
    a, captured, _client, _Block = _capturing_native("claude-sonnet-4-6")
    a.seed("do it", _tiny_png_b64(640, 400))
    asyncio.run(a.next_step())
    tool = captured["tools"][0]
    assert tool["type"] == "computer_20251124" and tool["name"] == "computer"
    assert tool["display_width_px"] == 640 and tool["display_height_px"] == 400
    assert tool["enable_zoom"] is True
    assert captured["extra_headers"]["anthropic-beta"] == "computer-use-2025-11-24"
    sysb = captured["system"]
    assert sysb[-1]["cache_control"] == {"type": "ephemeral"}       # prefix cache kept
    assert captured["output_config"] == {"effort": "medium"}        # effort inherited


def test_native_adapter_translates_and_scales():
    a, _captured, client, _Block = _capturing_native("claude-opus-4-8")
    a.seed("go", _tiny_png_b64(640, 400))
    a.screen_size = (1280, 800)  # frame 640x400 → scale 2.0
    client.messages.resp.content = [
        _Block(type="tool_use", id="t1", name="computer",
               input={"action": "left_click", "coordinate": [10, 20], "text": "ctrl+shift"}),
        _Block(type="tool_use", id="t2", name="computer",
               input={"action": "zoom", "region": [1, 2, 3, 4]}),
    ]
    res = asyncio.run(a.next_step())
    c1, c2 = res.calls
    assert c1.action == "click"
    assert c1.args["coordinate"] == [20, 40] and c1.args["button"] == "left"
    assert c1.args["modifiers"] == ["ctrl", "shift"]
    assert c2.action == "capture"
    assert c2.args == {"action": "capture", "mode": "vision", "region": [2, 4, 6, 8]}
    # History echoes the ORIGINAL native input, not the translation.
    tool_uses = [b for b in a.messages[-1]["content"] if b.get("type") == "tool_use"]
    assert tool_uses[0]["input"]["action"] == "left_click"


def test_native_translation_table():
    a, _c, _cl, _b = _capturing_native("claude-opus-4-8")   # no scale set → 1.0

    def t(inp):
        return a._translate(inp)

    assert t({"action": "screenshot"}) == {"action": "capture", "mode": "vision"}
    assert t({"action": "type", "text": "hi"}) == {"action": "type", "text": "hi"}
    assert t({"action": "key", "text": "Return"}) == {"action": "key", "keys": "Return"}
    assert t({"action": "hold_key", "text": "shift", "duration": 2}) == {
        "action": "hold_key", "keys": "shift", "seconds": 2}
    assert t({"action": "wait", "duration": 1.5}) == {"action": "wait", "seconds": 1.5}
    assert t({"action": "scroll", "coordinate": [5, 6], "scroll_direction": "up",
              "scroll_amount": 2}) == {
        "action": "scroll", "coordinate": [5, 6], "direction": "up", "amount": 2}
    assert t({"action": "left_click_drag", "start_coordinate": [1, 2], "coordinate": [3, 4]}) == {
        "action": "drag", "from_coordinate": [1, 2], "to_coordinate": [3, 4]}
    assert t({"action": "right_click", "coordinate": [7, 8]}) == {
        "action": "right_click", "coordinate": [7, 8]}
    assert t({"action": "cursor_position"}) == {"action": "cursor_position"}
    # Unknown native action passes through — executor errors, model adapts.
    assert t({"action": "made_up", "x": 1}) == {"action": "made_up", "x": 1}


def test_native_frame_dims_fixed_at_seed():
    a, _c, _cl, _b = _capturing_native("claude-sonnet-5")
    a.seed("go", _tiny_png_b64(640, 400))
    assert a._frame_size == (640, 400)
    # A zoom crop arriving later must NOT change the display declaration.
    a.add_results([ToolResult("t1", "{}", _tiny_png_b64(100, 50))])
    assert a._frame_size == (640, 400)
    tool = a._native_tool()
    assert (tool["display_width_px"], tool["display_height_px"]) == (640, 400)


def test_native_max_px_tiers():
    from computer_use_service import _native_max_px
    assert _native_max_px("claude-sonnet-5") == 2576
    assert _native_max_px("claude-opus-4-8") == 2576
    assert _native_max_px("claude-sonnet-4-6") == 1568
