# Multi-provider Computer Use — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the web `/computer-use` loop drive the desktop with Claude, OpenAI GPT-5.5, and Gemini via a provider-agnostic loop + one thin adapter per provider, all using the existing custom `computer_use` tool + SOM.

**Architecture:** Extract the model-call/parse logic out of `computer_use_service.run_loop` into a `CUAdapter` interface (one impl per provider, each using its own SDK). The loop, the `:0` executor (`handle_computer_use`), SOM screenshots, blocklist, and per-action approval stay shared and unchanged. `model id → provider` routing selects the adapter; each provider is gated on its API key.

**Tech Stack:** Python 3.13, aiohttp, `anthropic` 0.105.2, `openai` 2.38.0, `google-genai`; Next.js web for the picker. No new deps.

**Reference:** spec `docs/superpowers/specs/2026-06-20-multi-provider-computer-use-design.md`.

---

## File structure

- **Create** `src/voice-agent/pipeline/cu_adapters/__init__.py` — `provider_for(model)`, `make_adapter(model, system)`, `available_providers()`.
- **Create** `src/voice-agent/pipeline/cu_adapters/base.py` — `CUAdapter` ABC + dataclasses `ToolCall`, `ToolResult`, `StepResult`; the shared `strictify()` + `computer_use_tool_params()` helper.
- **Create** `src/voice-agent/pipeline/cu_adapters/anthropic_adapter.py` — `AnthropicCUAdapter`.
- **Create** `src/voice-agent/pipeline/cu_adapters/openai_adapter.py` — `OpenAICUAdapter` (gpt-5.5 via Chat Completions + function tool + image_url).
- **Create** `src/voice-agent/pipeline/cu_adapters/gemini_adapter.py` — `GeminiCUAdapter` (google-genai function declaration + inline image).
- **Modify** `src/voice-agent/computer_use_service.py` — `run_loop` becomes adapter-driven; `_ALLOWED_MODELS` += OpenAI/Gemini; `_resolve_model`/`_provider_for`; `/health` reports `available_providers()`.
- **Modify** `src/web/src/app/(app)/computer-use/page.tsx` — `CU_MODELS` += OpenAI/Gemini with a `native: true` marker; dim models whose provider has no key (from `/health`).
- **Create** `src/voice-agent/tests/test_cu_adapters.py` — adapter unit tests (SDKs mocked).
- **Create** `setup/systemd/jarvis-computer-use.service` — auto-start unit.

---

## Task 1: Adapter interface + shared types

**Files:**
- Create: `src/voice-agent/pipeline/cu_adapters/base.py`
- Test: `src/voice-agent/tests/test_cu_adapters.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cu_adapters.py
from pipeline.cu_adapters.base import ToolCall, ToolResult, StepResult, strictify

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_cu_adapters.py -q`
Expected: FAIL (module `pipeline.cu_adapters.base` not found).

- [ ] **Step 3: Write base.py**

```python
"""CUAdapter — provider-agnostic computer-use step interface.

Each provider adapter owns its SDK's message/tool/image format and parses
tool-calls into the uniform vocab; the loop in computer_use_service stays
provider-agnostic. The action vocab is the custom COMPUTER_USE_SCHEMA enum, so
handle_computer_use is unchanged for every provider.
"""
from __future__ import annotations

import copy
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ToolCall:
    id: str
    action: str
    args: Dict[str, Any]


@dataclass
class ToolResult:
    call_id: str
    text: str
    image_b64: Optional[str] = None


@dataclass
class StepResult:
    text: Optional[str]
    calls: List[ToolCall] = field(default_factory=list)


def strictify(node: Any) -> Any:
    """Recursively set additionalProperties:false on every object node (Anthropic
    rejects tool schemas without it; harmless for the others)."""
    if isinstance(node, dict):
        out = {k: strictify(v) for k, v in node.items()}
        if out.get("type") == "object" and "additionalProperties" not in out:
            out["additionalProperties"] = False
        return out
    if isinstance(node, list):
        return [strictify(v) for v in node]
    return node


def computer_use_tool_params() -> Dict[str, Any]:
    """The COMPUTER_USE_SCHEMA parameters object (strictified), shared by all
    adapters. Imported lazily to avoid a hard dep at module import."""
    from tools.computer_use import COMPUTER_USE_SCHEMA
    params = COMPUTER_USE_SCHEMA.get("parameters") or {"type": "object", "properties": {}}
    return strictify(copy.deepcopy(params))


def computer_use_description() -> str:
    from tools.computer_use import COMPUTER_USE_SCHEMA
    return COMPUTER_USE_SCHEMA["description"]


class CUAdapter(ABC):
    """One turn-driver per provider. Owns the provider-format conversation state."""

    def __init__(self, model: str, system: str) -> None:
        self.model = model
        self.system = system

    @abstractmethod
    def seed(self, task: str, image_b64: Optional[str]) -> None:
        """Append the first user turn (task text + optional screenshot)."""

    @abstractmethod
    async def next_step(self) -> StepResult:
        """Call the model; append the assistant turn; return text + tool calls."""

    @abstractmethod
    def add_results(self, results: List[ToolResult]) -> None:
        """Append tool results (each with the post-action screenshot) as the next
        user turn."""

    @abstractmethod
    def export_history(self) -> Any:
        """Image-free history snapshot for session persistence."""

    @abstractmethod
    def import_history(self, history: Any) -> None:
        """Restore a prior image-free history before seeding the new turn."""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_cu_adapters.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/voice-agent/pipeline/cu_adapters/base.py src/voice-agent/tests/test_cu_adapters.py
git commit -m "feat(cu): provider-agnostic CUAdapter interface + shared types"
```

---

## Task 2: AnthropicCUAdapter (refactor the existing Claude path)

**Files:**
- Create: `src/voice-agent/pipeline/cu_adapters/anthropic_adapter.py`
- Create: `src/voice-agent/pipeline/cu_adapters/__init__.py`
- Test: `src/voice-agent/tests/test_cu_adapters.py`

- [ ] **Step 1: Write the failing test** (mock the Anthropic client)

```python
def test_anthropic_adapter_parses_tool_use(monkeypatch):
    from pipeline.cu_adapters.anthropic_adapter import AnthropicCUAdapter

    class _Block:
        def __init__(self, **k): self.__dict__.update(k)
    class _Resp:
        content = [_Block(type="text", text="clicking"),
                   _Block(type="tool_use", id="t1", name="computer_use", input={"action": "click", "element": 3})]
    class _Msgs:
        def create(self, **k): return _Resp()
    class _Client:
        messages = _Msgs()

    a = AnthropicCUAdapter("claude-sonnet-4-6", "sys", client=_Client())
    a.seed("do it", "imgb64")
    import asyncio
    res = asyncio.get_event_loop().run_until_complete(a.next_step())
    assert res.text == "clicking"
    assert res.calls[0].action == "click" and res.calls[0].args["element"] == 3
    a.add_results([__import__("pipeline.cu_adapters.base", fromlist=["ToolResult"]).ToolResult("t1", "{}", "img2")])
    assert any(m["role"] == "user" for m in a.messages)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_cu_adapters.py -k anthropic -q`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement the adapter**

```python
"""AnthropicCUAdapter — Claude via the custom computer_use tool (SOM)."""
from __future__ import annotations

import asyncio
import os
from typing import Any, Dict, List, Optional

import anthropic

from .base import (CUAdapter, StepResult, ToolCall, ToolResult,
                   computer_use_description, computer_use_tool_params)


def _img_block(b64: str) -> Dict[str, Any]:
    return {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}}


class AnthropicCUAdapter(CUAdapter):
    def __init__(self, model: str, system: str, client: Optional[Any] = None) -> None:
        super().__init__(model, system)
        self._client = client or anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
        self._tool = {"name": "computer_use", "description": computer_use_description(),
                      "input_schema": computer_use_tool_params()}
        self.messages: List[Dict[str, Any]] = []

    def seed(self, task: str, image_b64: Optional[str]) -> None:
        content: List[Dict[str, Any]] = [{"type": "text", "text": task}]
        if image_b64:
            content.append(_img_block(image_b64))
        self.messages.append({"role": "user", "content": content})

    async def next_step(self) -> StepResult:
        resp = await asyncio.to_thread(
            self._client.messages.create, model=self.model, max_tokens=4096,
            system=self.system, messages=self.messages, tools=[self._tool])
        assistant: List[Dict[str, Any]] = []
        calls: List[ToolCall] = []
        text_out: Optional[str] = None
        for b in resp.content:
            if b.type == "text":
                assistant.append({"type": "text", "text": b.text})
                text_out = (text_out or "") + b.text
            elif b.type == "tool_use":
                assistant.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
                args = dict(b.input) if isinstance(b.input, dict) else {}
                calls.append(ToolCall(id=b.id, action=str(args.get("action") or "?"), args=args))
        self.messages.append({"role": "assistant", "content": assistant})
        return StepResult(text=(text_out.strip() if text_out else None), calls=calls)

    def add_results(self, results: List[ToolResult]) -> None:
        blocks: List[Dict[str, Any]] = []
        for r in results:
            content: List[Dict[str, Any]] = [{"type": "text", "text": r.text}]
            if r.image_b64:
                content.append(_img_block(r.image_b64))
            blocks.append({"type": "tool_result", "tool_use_id": r.call_id, "content": content})
        self.messages.append({"role": "user", "content": blocks})

    def export_history(self) -> Any:
        return _strip_images(self.messages)

    def import_history(self, history: Any) -> None:
        self.messages = list(history or [])


def _strip_images(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    def clean(blocks: List[Any]) -> List[Any]:
        out: List[Any] = []
        for b in blocks:
            if not isinstance(b, dict):
                out.append(b); continue
            if b.get("type") == "image":
                continue
            if b.get("type") == "tool_result" and isinstance(b.get("content"), list):
                out.append({**b, "content": clean(b["content"]) or [{"type": "text", "text": "(screenshot)"}]})
            else:
                out.append(b)
        return out
    res: List[Dict[str, Any]] = []
    for m in messages:
        c = m.get("content")
        res.append({**m, "content": clean(c) or [{"type": "text", "text": "(screenshot)"}]} if isinstance(c, list) else dict(m))
    return res
```

- [ ] **Step 4: Write `__init__.py` (factory + routing + availability)**

```python
"""cu_adapters — provider routing + factory for the computer-use loop."""
from __future__ import annotations

import os
from typing import Dict, List

from .base import CUAdapter

_DEFAULT_MODEL = os.environ.get("JARVIS_COMPUTER_USE_WEB_MODEL", "claude-sonnet-4-6")


def provider_for(model: str) -> str:
    m = (model or "").lower()
    if m.startswith("gpt-") or m.startswith("o1") or m.startswith("o3"):
        return "openai"
    if m.startswith("gemini-"):
        return "gemini"
    return "anthropic"


def _key_for(provider: str) -> str:
    return {
        "anthropic": os.environ.get("ANTHROPIC_API_KEY", ""),
        "openai": os.environ.get("OPENAI_API_KEY", ""),
        "gemini": os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY", ""),
    }.get(provider, "")


def available_providers() -> Dict[str, bool]:
    return {p: bool(_key_for(p)) for p in ("anthropic", "openai", "gemini")}


def make_adapter(model: str, system: str) -> CUAdapter:
    provider = provider_for(model)
    if provider == "openai":
        from .openai_adapter import OpenAICUAdapter
        return OpenAICUAdapter(model, system)
    if provider == "gemini":
        from .gemini_adapter import GeminiCUAdapter
        return GeminiCUAdapter(model, system)
    from .anthropic_adapter import AnthropicCUAdapter
    return AnthropicCUAdapter(model, system)
```

- [ ] **Step 5: Run + commit**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_cu_adapters.py -k anthropic -q` → PASS.
```bash
git add src/voice-agent/pipeline/cu_adapters/
git commit -m "feat(cu): AnthropicCUAdapter + provider routing/factory"
```

---

## Task 3: Wire the loop to the adapter (behavior-preserving for Claude)

**Files:**
- Modify: `src/voice-agent/computer_use_service.py` (`run_loop`)

- [ ] **Step 1:** Replace the Anthropic-specific body of `run_loop` with adapter-driven logic, keeping blocklist + approval + SOM exactly as-is. Sessions now store the adapter's `export_history()` keyed by `(session_id, provider)`.

```python
from pipeline.cu_adapters import make_adapter, provider_for

# _SESSIONS now maps session_id -> {provider: history}
async def run_loop(task, session_id, supervised, model, emit):
    if not x11_backend_available():
        await emit({"type": "error", "error": "No X11 display — needs a desktop (DISPLAY=:0)."}); return
    provider = provider_for(model)
    adapter = make_adapter(model, SYSTEM_PROMPT)
    prior = _SESSIONS.get(session_id, {}).get(provider)
    if prior is not None:
        adapter.import_history(prior)
    await asyncio.to_thread(_ensure_som)
    adapter.seed(task, (_current_frame_block() or {}).get("source", {}).get("data") if False else _current_frame_b64())
    try:
        for _ in range(MAX_STEPS):
            try:
                res = await adapter.next_step()
            except Exception as e:  # noqa: BLE001
                await emit({"type": "error", "error": f"model call failed: {e}"}); return
            if res.text:
                await emit({"type": "text", "text": res.text})
            if not res.calls:
                await emit({"type": "done"}); return
            results = []
            for call in res.calls:
                blocked = _blocked_app(call.args) if call.action in ("focus_app", "launch") else None
                if blocked:
                    await emit({"type": "blocked", "summary": f"Blocked “{blocked}” (sensitive-app policy)"})
                    results.append(__mk_result(call.id, json.dumps({"error": f"app '{blocked}' is blocklisted."})))
                    continue
                kind = _approval_kind(call.action)
                if supervised and kind and kind not in _APPROVED_KINDS.get(session_id, set()):
                    decision = await _ask_approval(call.action, _summarize_action(call.action, call.args), emit)
                    if decision == "deny":
                        await emit({"type": "denied", "summary": _summarize_action(call.action, call.args)})
                        results.append(__mk_result(call.id, json.dumps({"error": "user denied this action."})))
                        continue
                    if decision == "session":
                        _APPROVED_KINDS.setdefault(session_id, set()).add(kind)
                await emit({"type": "action", "summary": _summarize_action(call.action, call.args)})
                try:
                    out = await asyncio.to_thread(handle_computer_use, {"action": call.action, **call.args})
                except Exception as e:  # noqa: BLE001
                    out = json.dumps({"error": f"{call.action} failed: {e}"})
                if call.action != "capture":
                    await asyncio.to_thread(_ensure_som)
                results.append(__mk_result(call.id, out, _current_frame_b64()))
            adapter.add_results(results)
        await emit({"type": "error", "error": f"hit step cap ({MAX_STEPS}) — stopping."})
    finally:
        _SESSIONS.setdefault(session_id, {})[provider] = adapter.export_history()
```

Add helpers `_current_frame_b64()` (returns the downscaled SOM png b64 or None — factor out of `_current_frame_block`) and `__mk_result(call_id, text, img=None)` → `ToolResult`. Remove the old inline Anthropic loop + `_strip_images`/`_trim_history` (now in the adapter).

- [ ] **Step 2: Run the existing service smoke + adapter tests**

Run: `cd src/voice-agent && .venv/bin/python -c "import computer_use_service as s; s.build_app(); print('ok')"` → ok.
Run: `.venv/bin/python -m pytest tests/test_cu_adapters.py -q` → PASS.

- [ ] **Step 3: Commit**

```bash
git add src/voice-agent/computer_use_service.py
git commit -m "refactor(cu): drive the loop through CUAdapter (Claude path preserved)"
```

---

## Task 4: OpenAICUAdapter (GPT-5.5)

**Files:**
- Create: `src/voice-agent/pipeline/cu_adapters/openai_adapter.py`
- Test: `src/voice-agent/tests/test_cu_adapters.py`

- [ ] **Step 1: Write the failing test** (mock the OpenAI client; assert the tool is a function + image is image_url + tool_calls parse).

```python
def test_openai_adapter_parses_tool_calls():
    from pipeline.cu_adapters.openai_adapter import OpenAICUAdapter
    from pipeline.cu_adapters.base import ToolResult
    import asyncio, json
    class _Fn: name="computer_use"; arguments=json.dumps({"action":"type","text":"hi"})
    class _TC: id="c1"; type="function"; function=_Fn()
    class _Msg: content="typing"; tool_calls=[_TC()]
    class _Choice: message=_Msg()
    class _Resp: choices=[_Choice()]
    class _Comp:
        def create(self, **k): return _Resp()
    class _Client:
        chat = type("C", (), {"completions": _Comp()})()
    a = OpenAICUAdapter("gpt-5.5", "sys", client=_Client())
    a.seed("do it", "imgb64")
    res = asyncio.get_event_loop().run_until_complete(a.next_step())
    assert res.calls[0].action == "type" and res.calls[0].args["text"] == "hi"
    a.add_results([ToolResult("c1", "{}", "img2")])
    assert a.messages[-1]["role"] == "tool"
```

- [ ] **Step 2: Run → FAIL** (`-k openai`).

- [ ] **Step 3: Implement** (Chat Completions; `computer_use` as a function tool; screenshots as `image_url` data URLs; results as role:`tool` messages).

```python
"""OpenAICUAdapter — GPT-5.5 driving the custom computer_use tool (function calling + vision)."""
from __future__ import annotations
import asyncio, json, os
from typing import Any, Dict, List, Optional
import openai
from .base import (CUAdapter, StepResult, ToolCall, ToolResult,
                   computer_use_description, computer_use_tool_params)

def _img_part(b64: str) -> Dict[str, Any]:
    return {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}

class OpenAICUAdapter(CUAdapter):
    def __init__(self, model: str, system: str, client: Optional[Any] = None) -> None:
        super().__init__(model, system)
        self._client = client or openai.OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        self._tools = [{"type": "function", "function": {
            "name": "computer_use", "description": computer_use_description(),
            "parameters": computer_use_tool_params()}}]
        self.messages: List[Dict[str, Any]] = [{"role": "system", "content": system}]

    def seed(self, task: str, image_b64: Optional[str]) -> None:
        content: List[Dict[str, Any]] = [{"type": "text", "text": task}]
        if image_b64:
            content.append(_img_part(image_b64))
        self.messages.append({"role": "user", "content": content})

    async def next_step(self) -> StepResult:
        resp = await asyncio.to_thread(self._client.chat.completions.create,
                                       model=self.model, messages=self.messages, tools=self._tools)
        msg = resp.choices[0].message
        calls: List[ToolCall] = []
        raw_tcs = getattr(msg, "tool_calls", None) or []
        self.messages.append({"role": "assistant", "content": msg.content or "",
                              "tool_calls": [{"id": tc.id, "type": "function",
                                              "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                                             for tc in raw_tcs]})
        for tc in raw_tcs:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            calls.append(ToolCall(id=tc.id, action=str(args.get("action") or "?"), args=args))
        return StepResult(text=(msg.content or None), calls=calls)

    def add_results(self, results: List[ToolResult]) -> None:
        for r in results:
            self.messages.append({"role": "tool", "tool_call_id": r.call_id, "content": r.text})
        # OpenAI carries the screenshot as a follow-up user image (tool role can't hold images).
        imgs = [_img_part(r.image_b64) for r in results if r.image_b64]
        if imgs:
            self.messages.append({"role": "user", "content": [{"type": "text", "text": "Current screen:"}, *imgs]})

    def export_history(self) -> Any:
        return [m for m in self.messages if not (isinstance(m.get("content"), list))] + \
               [{"role": "system", "content": "(screenshots elided)"}] if False else _strip(self.messages)

    def import_history(self, history: Any) -> None:
        self.messages = list(history or [{"role": "system", "content": self.system}])

def _strip(messages):
    out = []
    for m in messages:
        c = m.get("content")
        if isinstance(c, list):
            kept = [b for b in c if not (isinstance(b, dict) and b.get("type") == "image_url")]
            out.append({**m, "content": kept or [{"type": "text", "text": "(screenshot)"}]})
        else:
            out.append(dict(m))
    return out
```

- [ ] **Step 4: Run → PASS** (`-k openai`).
- [ ] **Step 5: Commit** `feat(cu): OpenAICUAdapter (GPT-5.5)`.

---

## Task 5: GeminiCUAdapter

**Files:**
- Create: `src/voice-agent/pipeline/cu_adapters/gemini_adapter.py`
- Test: `src/voice-agent/tests/test_cu_adapters.py`

- [ ] **Step 1: Write the failing test** (mock `google.genai` client; assert function_call parse + inline image part).

```python
def test_gemini_adapter_parses_function_calls():
    from pipeline.cu_adapters.gemini_adapter import GeminiCUAdapter
    from pipeline.cu_adapters.base import ToolResult
    import asyncio
    class _FC: name="computer_use"; args={"action":"scroll","element":2,"direction":"down"}
    class _Part: text=None; function_call=_FC()
    class _Content: parts=[_Part()]
    class _Cand: content=_Content()
    class _Resp: candidates=[_Cand()]; text=None
    class _Models:
        def generate_content(self, **k): return _Resp()
    class _Client: models=_Models()
    a = GeminiCUAdapter("gemini-3-flash-preview", "sys", client=_Client())
    a.seed("do it", "imgb64")
    res = asyncio.get_event_loop().run_until_complete(a.next_step())
    assert res.calls[0].action == "scroll" and res.calls[0].args["element"] == 2
    a.add_results([ToolResult("computer_use", "{}", "img2")])
    assert len(a.contents) >= 2
```

- [ ] **Step 2: Run → FAIL** (`-k gemini`).

- [ ] **Step 3: Implement** (`google.genai`; `computer_use` as a `FunctionDeclaration`; screenshots as inline image parts; results as `function_response` parts).

```python
"""GeminiCUAdapter — Gemini driving the custom computer_use tool."""
from __future__ import annotations
import asyncio, base64, os
from typing import Any, Dict, List, Optional
from google import genai
from google.genai import types
from .base import (CUAdapter, StepResult, ToolCall, ToolResult,
                   computer_use_description, computer_use_tool_params)

class GeminiCUAdapter(CUAdapter):
    def __init__(self, model: str, system: str, client: Optional[Any] = None) -> None:
        super().__init__(model, system)
        self._client = client or genai.Client(api_key=os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
        self._tool = types.Tool(function_declarations=[types.FunctionDeclaration(
            name="computer_use", description=computer_use_description(),
            parameters=computer_use_tool_params())])
        self.contents: List[Any] = []

    def _img(self, b64: str):
        return types.Part.from_bytes(data=base64.b64decode(b64), mime_type="image/png")

    def seed(self, task: str, image_b64: Optional[str]) -> None:
        parts = [types.Part(text=task)]
        if image_b64:
            parts.append(self._img(image_b64))
        self.contents.append(types.Content(role="user", parts=parts))

    async def next_step(self) -> StepResult:
        resp = await asyncio.to_thread(
            self._client.models.generate_content, model=self.model, contents=self.contents,
            config=types.GenerateContentConfig(system_instruction=self.system, tools=[self._tool]))
        cand = resp.candidates[0]
        self.contents.append(cand.content)
        calls: List[ToolCall] = []
        text_out: Optional[str] = None
        for p in cand.content.parts:
            if getattr(p, "text", None):
                text_out = (text_out or "") + p.text
            fc = getattr(p, "function_call", None)
            if fc:
                args = dict(fc.args or {})
                calls.append(ToolCall(id=fc.name, action=str(args.get("action") or "?"), args=args))
        return StepResult(text=(text_out.strip() if text_out else None), calls=calls)

    def add_results(self, results: List[ToolResult]) -> None:
        parts = [types.Part.from_function_response(name=r.call_id, response={"result": r.text}) for r in results]
        for r in results:
            if r.image_b64:
                parts.append(self._img(r.image_b64))
        self.contents.append(types.Content(role="user", parts=parts))

    def export_history(self) -> Any:
        # Gemini Content objects aren't trivially serialisable for persistence;
        # v1 keeps history in-process only (session continuity within the run).
        return None

    def import_history(self, history: Any) -> None:
        return
```

- [ ] **Step 4: Run → PASS** (`-k gemini`).
- [ ] **Step 5: Commit** `feat(cu): GeminiCUAdapter`.

---

## Task 6: Provider gating in the sidecar (`_ALLOWED_MODELS`, `/health`)

**Files:**
- Modify: `src/voice-agent/computer_use_service.py`

- [ ] **Step 1:** Expand `_ALLOWED_MODELS` to include `gpt-5.5`, `gpt-5.5-pro`, `gemini-3-flash-preview`, plus the Claude ids. In `_run`, after `_resolve_model`, if `available_providers()[provider_for(model)]` is False, return an SSE error: `"<provider> has no API key configured."`. In `_health`, add `"providers": available_providers()`.

- [ ] **Step 2: Test** the health payload includes providers.

```python
def test_health_reports_providers():
    import computer_use_service as s
    from aiohttp.test_utils import make_mocked_request
    import asyncio
    r = asyncio.get_event_loop().run_until_complete(s._health(make_mocked_request("GET", "/health")))
    import json
    body = json.loads(r.body.decode())
    assert "providers" in body and "openai" in body["providers"]
```

Run: `.venv/bin/python -m pytest tests/test_cu_adapters.py -k health -q` → PASS.

- [ ] **Step 3: Commit** `feat(cu): provider key-gating + /health providers`.

---

## Task 7: Web picker — add OpenAI/Gemini, native marker, dim unavailable

**Files:**
- Modify: `src/web/src/app/(app)/computer-use/page.tsx`

- [ ] **Step 1:** Extend `CU_MODELS` with the OpenAI + Gemini entries and a `native: true` flag on all; fetch `/api/computer-use` (the GET already returns status — add `providers` passthrough in the route) and dim models whose provider is unavailable. Render a tiny "native" marker per the spec.

```tsx
const CU_MODELS = [
  { id: "claude-sonnet-4-6", label: "Claude Sonnet 4.6", hint: "Balanced", provider: "anthropic", native: true },
  { id: "claude-opus-4-8", label: "Claude Opus 4.8", hint: "Most capable", provider: "anthropic", native: true },
  { id: "claude-haiku-4-5", label: "Claude Haiku 4.5", hint: "Fastest", provider: "anthropic", native: true },
  { id: "gpt-5.5", label: "GPT-5.5", hint: "OpenAI", provider: "openai", native: true },
  { id: "gemini-3-flash-preview", label: "Gemini 3 Flash", hint: "Google", provider: "gemini", native: true },
] as const;
```

- [ ] **Step 2:** In the GET route + `/health`, surface `providers`; in the page, read it into state and pass `disabled` + a dimmed style to `DropdownMenuItem` for models whose provider is unavailable.

- [ ] **Step 3: Verify** `cd src/web && npx eslint "src/app/(app)/computer-use/page.tsx"` → 0 errors.
- [ ] **Step 4: Commit** `feat(web): multi-provider model picker for /computer-use`.

---

## Task 8: systemd auto-start (operational simplicity)

**Files:**
- Create: `setup/systemd/jarvis-computer-use.service`

- [ ] **Step 1:** Write a `--user` unit that runs the sidecar (loads `keys.env` via the sidecar's own `_load_env_files`), `Restart=on-failure`.

```ini
[Unit]
Description=JARVIS computer-use sidecar (:8771)
After=default.target

[Service]
WorkingDirectory=%h/Documents/Projects/jarvis/src/voice-agent
ExecStart=%h/Documents/Projects/jarvis/src/voice-agent/.venv/bin/python computer_use_service.py
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
```

- [ ] **Step 2:** Document enable steps in the runbook (don't enable automatically): `systemctl --user enable --now jarvis-computer-use.service`.
- [ ] **Step 3: Commit** `feat(cu): systemd --user unit for the sidecar`.

---

## Self-review notes

- **Spec coverage:** adapters (T1,2,4,5), loop wiring (T3), routing/gating (T2,6), web picker + native marker (T7), systemd (T8), testing (per-task mocked SDK tests). Native-CU vs uniform-SOM: uniform path implemented; native deferred (noted).
- **Type consistency:** `ToolCall(id,action,args)`, `ToolResult(call_id,text,image_b64)`, `StepResult(text,calls)`, `CUAdapter.{seed,next_step,add_results,export_history,import_history}` used identically across all adapters + the loop.
- **Known v1 limitation:** Gemini history isn't persisted across `/run` calls (Content objects); session continuity holds within a run. Anthropic/OpenAI persist image-free history. Acceptable for v1; revisit if cross-turn Gemini memory matters.
- **Open item:** confirm the exact Gemini 2.5 Computer Use model id + whether `gemini-3-flash-preview` is the right GA id at implementation (provider-availability + a live ping).
