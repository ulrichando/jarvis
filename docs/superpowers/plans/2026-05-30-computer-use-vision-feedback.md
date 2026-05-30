# `computer_use` Vision-Feedback Loop (P2a) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** after a `computer_use` capture, the supervisor's next generation includes the post-action screen — the real screenshot (pixels) when the active model is vision-capable, a fresh text description otherwise — so it plans/verifies from what's on screen instead of acting blind.

**Architecture:** `computer_use` publishes its newest screenshot to a small ephemeral cache (`pipeline/computer_use_vision.py`). `JarvisAgent.llm_node` (a new async-generator override) reads the cache and injects one `ChatMessage` (an `ImageContent`, or a text description) into the **per-generation `chat_ctx` copy** the framework hands it — so the injection is ephemeral (never pollutes canonical history; eviction is free). Provider-agnostic: `ImageContent` → native image block via the framework's central `to_provider_format`. Gated by `JARVIS_CU_VISION_MODE` (default `auto` → best-effort detect the active route's model; defaults to pixels since every per-route supervisor default is Claude).

**Tech Stack:** Python 3.13, livekit-agents 1.5.14 (`ImageContent`, `Agent.default.llm_node`), Pillow 12.2 (downscale), stdlib, `pytest`. Voice-agent venv: `src/voice-agent/.venv/bin/python`. Spec: `docs/superpowers/specs/2026-05-30-computer-use-vision-feedback-design.md`.

---

## File Structure
- **Create** `src/voice-agent/pipeline/computer_use_vision.py` — ephemeral newest-frame cache + recent-action trail + vision gate + downscale + injection-decision helpers. No livekit/providers imports at module load (lazy, inside functions). Pure + unit-testable.
- **Modify** `src/voice-agent/providers/llm.py` — add public `resolve_route_primary_model(route) -> str` (mirrors the nested `_resolve_route_model`, rung-1 model only) for the gate.
- **Modify** `src/voice-agent/tools/computer_use.py` — `_capture_response` publishes the frame; `handle_computer_use` records the action label. Both best-effort. **No schema change.**
- **Modify** `src/voice-agent/jarvis_agent.py` — add `JarvisAgent.llm_node` override (inject) + stash `_jarvis_agent._dispatch_llm` at build time + `computer_use_vision.clear()` in the existing `on_user_turn_completed`.
- **Create** `src/voice-agent/tests/test_computer_use_vision.py`.

**Out of scope (do NOT touch):** the `computer_use` schema (keeps the `anthropic_strict_schema` patch untouched); `pipeline/automod/`; AT-SPI2/Set-of-Marks (P2b); self-verification (P2c); the stale "no accessibility tree" comments (P2b); the uncommitted `dispatch_agent`/`background_tasks` WIP in the tree (stage only the files each task lists).

**Verified surfaces (don't re-derive):** `ImageContent(image="data:image/png;base64,…", inference_detail="auto")` is a `ChatContent`; `chat_ctx.add_message(*, role, content: list[ChatContent]|str)`; `Agent.default.llm_node(self, chat_ctx, tools, model_settings)` is the delegation target (async-iterable → `async for … yield`); `chat_ctx` passed to `llm_node` is a per-generation copy (`agent_activity.py:2512`); `CaptureResult` has `.png_b64/.width/.height`; `_ANTH_DEFAULT_PER_ROUTE` (llm.py:929) + `_specialty` (llm.py:47) are module-level; `_dispatch_llm` is local to `entrypoint()` (stash it on the agent); `screen_share_observer.latest_description_global()` is the text fallback.

---

### Task 1: vision cache + trail + capability gate (`computer_use_vision.py`)

**Files:** Create `src/voice-agent/pipeline/computer_use_vision.py`, `src/voice-agent/tests/test_computer_use_vision.py`.

- [ ] **Step 1: Write the failing tests** — create `tests/test_computer_use_vision.py`:

```python
from pipeline import computer_use_vision as cuv


def setup_function(_):
    cuv.clear()


def test_publish_take_newest_and_ttl():
    cuv.publish_capture(png_b64="AAAA", width=800, height=600, action_label="capture", _now=100.0)
    cuv.publish_capture(png_b64="BBBB", width=10, height=10, action_label="capture", _now=101.0)
    cur = cuv.take_current(_now=101.0)            # newest wins
    assert cur["png_b64"] == "BBBB" and cur["width"] == 10
    assert cuv.take_current(ttl_s=20.0, _now=130.0) is None   # past TTL
    assert cuv.take_current(_now=101.0) is not None            # non-consuming


def test_clear_empties_cache_and_trail():
    cuv.publish_capture(png_b64="AAAA", width=1, height=1, _now=1.0)
    cuv.record_action("left_click @ (10,20)")
    cuv.clear()
    assert cuv.take_current(_now=1.0) is None
    assert cuv.recent_actions_text() == ""


def test_record_action_trail_caps_at_3():
    for lbl in ["a", "b", "c", "d"]:
        cuv.record_action(lbl)
    txt = cuv.recent_actions_text()
    assert "d" in txt and "a" not in txt          # deque maxlen=3 evicts oldest
    assert txt.startswith(" (recent:")


def test_publish_ignores_empty_png():
    cuv.publish_capture(png_b64=None, width=1, height=1, _now=1.0)
    assert cuv.take_current(_now=1.0) is None


def test_is_vision_capable():
    assert cuv.is_vision_capable("claude-sonnet-4-6") is True
    assert cuv.is_vision_capable("claude-haiku-4-5") is True
    assert cuv.is_vision_capable("gpt-4o") is True
    assert cuv.is_vision_capable("gemini-2.5-flash") is True
    assert cuv.is_vision_capable("llama-3.3-70b-versatile") is False
    assert cuv.is_vision_capable("deepseek-v4-flash") is False
    assert cuv.is_vision_capable("") is False and cuv.is_vision_capable(None) is False


def test_is_vision_capable_env_override(monkeypatch):
    monkeypatch.setenv("JARVIS_VISION_MODEL_PREFIXES", "llama-,foo-")
    assert cuv.is_vision_capable("llama-3.3-70b-versatile") is True
    assert cuv.is_vision_capable("claude-sonnet-4-6") is False
```

- [ ] **Step 2: Run → FAIL**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_computer_use_vision.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'pipeline.computer_use_vision'`.

- [ ] **Step 3: Implement** — create `src/voice-agent/pipeline/computer_use_vision.py`:

```python
"""Ephemeral newest-frame cache + vision gate for the computer_use vision-feedback
loop (Web-Nav P2a). Standalone — no livekit/providers imports at module load (they
are lazy, inside functions). JarvisAgent.llm_node reads this to inject the
post-action screen into the per-generation chat_ctx copy.
"""
from __future__ import annotations
import base64
import io
import os
import time
from collections import deque
from typing import Optional

_VISION_PREFIXES_DEFAULT = ("claude-", "gpt-4o", "gpt-4.1", "gemini-")
_DEFAULT_TTL_S = 20.0
_MAX_DOWNSCALE_PX = 1280
_TRAIL_MAXLEN = 3

_latest: Optional[dict] = None
_recent: "deque[str]" = deque(maxlen=_TRAIL_MAXLEN)


def publish_capture(*, png_b64: Optional[str], width, height,
                    action_label: str = "capture", _now: Optional[float] = None) -> None:
    """Store the newest screenshot frame (overwrites any prior). No-op if no png."""
    global _latest
    if not png_b64:
        return
    ts = _now if _now is not None else time.monotonic()
    _latest = {"png_b64": png_b64, "width": int(width or 0), "height": int(height or 0),
               "action_label": action_label or "capture", "ts": ts}


def record_action(label: str) -> None:
    """Append a short action label to the recent-actions trail (cheap context)."""
    if label:
        _recent.append(label)


def take_current(ttl_s: float = _DEFAULT_TTL_S, _now: Optional[float] = None) -> Optional[dict]:
    """Return a copy of the newest frame if within ttl_s (non-consuming), else None."""
    if _latest is None:
        return None
    now = _now if _now is not None else time.monotonic()
    if (now - _latest["ts"]) > ttl_s:
        return None
    return dict(_latest)


def clear() -> None:
    """Drop the cached frame + trail (call on a new user turn)."""
    global _latest
    _latest = None
    _recent.clear()


def recent_actions_text() -> str:
    labels = list(_recent)
    return f" (recent: {', '.join(labels)})" if labels else ""


def is_vision_capable(model_id: Optional[str], prefixes=None) -> bool:
    if not model_id:
        return False
    if prefixes is None:
        env = os.environ.get("JARVIS_VISION_MODEL_PREFIXES", "").strip()
        prefixes = tuple(p.strip() for p in env.split(",") if p.strip()) or _VISION_PREFIXES_DEFAULT
    mid = model_id.lower()
    return any(mid.startswith(p.lower()) for p in prefixes)
```

- [ ] **Step 4: Run → PASS**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_computer_use_vision.py -q`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add src/voice-agent/pipeline/computer_use_vision.py src/voice-agent/tests/test_computer_use_vision.py
git commit -m "feat(computer_use): vision cache + recent-action trail + capability gate"
```

---

### Task 2: `downscale_png` (Pillow)

**Files:** Modify `src/voice-agent/pipeline/computer_use_vision.py`, `tests/test_computer_use_vision.py`.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_computer_use_vision.py`:

```python
import base64, io


def _png_b64(w, h):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (123, 50, 200)).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def test_downscale_png_shrinks_large():
    from PIL import Image
    out = cuv.downscale_png(_png_b64(2400, 1200), max_px=1280)
    assert out is not None
    img = Image.open(io.BytesIO(base64.b64decode(out)))
    assert max(img.size) <= 1280 and img.size[0] >= img.size[1]   # aspect preserved


def test_downscale_png_keeps_small():
    from PIL import Image
    out = cuv.downscale_png(_png_b64(400, 300), max_px=1280)
    img = Image.open(io.BytesIO(base64.b64decode(out)))
    assert img.size == (400, 300)


def test_downscale_png_bad_input_returns_none():
    assert cuv.downscale_png("not-base64-@@@") is None
    assert cuv.downscale_png("") is None
```

- [ ] **Step 2: Run → FAIL**

Run: `.venv/bin/python -m pytest tests/test_computer_use_vision.py -q -k downscale`
Expected: FAIL with `AttributeError: module 'pipeline.computer_use_vision' has no attribute 'downscale_png'`.

- [ ] **Step 3: Implement** — append to `pipeline/computer_use_vision.py`:

```python
def downscale_png(png_b64: str, max_px: int = _MAX_DOWNSCALE_PX) -> Optional[str]:
    """Downscale a base64 PNG so its longest edge <= max_px (aspect preserved);
    return a new base64 PNG. Unchanged if already small. None on any error."""
    if not png_b64:
        return None
    try:
        raw = base64.b64decode(png_b64, validate=True)
    except Exception:
        return None
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(raw))
        img.load()
        w, h = img.size
        longest = max(w, h)
        if longest > max_px and longest > 0:
            scale = max_px / float(longest)
            img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))))
        if img.mode not in ("RGB", "RGBA", "L"):
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        return None
```

- [ ] **Step 4: Run → PASS**

Run: `.venv/bin/python -m pytest tests/test_computer_use_vision.py -q`
Expected: PASS (10 tests).

- [ ] **Step 5: Commit**

```bash
git add src/voice-agent/pipeline/computer_use_vision.py src/voice-agent/tests/test_computer_use_vision.py
git commit -m "feat(computer_use): downscale_png helper to bound vision tokens"
```

---

### Task 3: mode decision + injection builder (`decide_mode`, `build_injection`)

**Files:** Modify `src/voice-agent/pipeline/computer_use_vision.py`, `tests/test_computer_use_vision.py`.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_computer_use_vision.py`:

```python
class _FakeDispatch:
    def __init__(self, route):
        self.last_route = route


def test_decide_mode_explicit(monkeypatch):
    for m in ("pixels", "text", "off"):
        monkeypatch.setenv("JARVIS_CU_VISION_MODE", m)
        assert cuv.decide_mode(None) == m


def test_decide_mode_auto_defaults_pixels_without_dispatch(monkeypatch):
    monkeypatch.delenv("JARVIS_CU_VISION_MODE", raising=False)
    assert cuv.decide_mode(None) == "pixels"        # uncertainty → pixels (Claude default)


def test_decide_mode_auto_text_only_route(monkeypatch):
    monkeypatch.delenv("JARVIS_CU_VISION_MODE", raising=False)
    # Force the route's model text-only via the route's env override.
    monkeypatch.setenv("JARVIS_TASK_DESKTOP_MODEL", "llama-3.3-70b-versatile")
    assert cuv.decide_mode(_FakeDispatch("TASK_DESKTOP")) == "text"


def test_decide_mode_auto_vision_route(monkeypatch):
    monkeypatch.delenv("JARVIS_CU_VISION_MODE", raising=False)
    monkeypatch.delenv("JARVIS_TASK_DESKTOP_MODEL", raising=False)
    monkeypatch.delenv("JARVIS_TASK_MODEL", raising=False)
    assert cuv.decide_mode(_FakeDispatch("TASK_DESKTOP")) == "pixels"   # default claude-sonnet


def test_build_injection_pixels():
    from livekit.agents.llm import ImageContent
    cap = {"png_b64": _png_b64(100, 80), "action_label": "capture"}
    res = cuv.build_injection(cap=cap, mode="pixels")
    assert res is not None
    role, content = res
    assert role == "user"
    assert any(isinstance(c, ImageContent) for c in content)
    assert any(isinstance(c, str) and "screen after" in c for c in content)


def test_build_injection_text():
    cap = {"png_b64": "x", "action_label": "capture"}
    res = cuv.build_injection(cap=cap, mode="text", desc="A settings window is open.")
    role, content = res
    assert role == "user" and "settings window" in content[0]


def test_build_injection_none_cases():
    assert cuv.build_injection(cap=None, mode="pixels") is None
    assert cuv.build_injection(cap={"png_b64": "x"}, mode="off") is None
    assert cuv.build_injection(cap={"png_b64": "x", "action_label": "c"}, mode="text", desc=None) is None
```

- [ ] **Step 2: Run → FAIL**

Run: `.venv/bin/python -m pytest tests/test_computer_use_vision.py -q -k "decide_mode or build_injection"`
Expected: FAIL with `AttributeError: ... has no attribute 'decide_mode'`.

- [ ] **Step 3: Implement** — append to `pipeline/computer_use_vision.py`:

```python
def decide_mode(dispatch_llm=None) -> str:
    """Resolve JARVIS_CU_VISION_MODE. Explicit pixels/text/off win. 'auto' (default)
    best-effort detects the active route's model via dispatch_llm.last_route +
    providers.llm.resolve_route_primary_model; defaults to 'pixels' on any
    uncertainty (the canonical supervisor is Claude / vision-capable)."""
    mode = (os.environ.get("JARVIS_CU_VISION_MODE", "auto").strip().lower() or "auto")
    if mode in ("pixels", "text", "off"):
        return mode
    try:
        route = getattr(dispatch_llm, "last_route", None)
        if route:
            from providers.llm import resolve_route_primary_model
            model = resolve_route_primary_model(route)
            return "pixels" if is_vision_capable(model) else "text"
    except Exception:
        pass
    return "pixels"


def build_injection(*, cap: Optional[dict], mode: str, desc: Optional[str] = None):
    """Return (role, content_list) to add to chat_ctx, or None for no injection.
    pixels → text label + downscaled ImageContent; text → label + description."""
    if not cap or mode == "off":
        return None
    label = cap.get("action_label") or "computer_use"
    trail = recent_actions_text()
    if mode == "pixels":
        b64 = downscale_png(cap.get("png_b64") or "")
        if not b64:
            return None
        from livekit.agents.llm import ImageContent
        return ("user", [f"[screen after: {label}]{trail}",
                         ImageContent(image="data:image/png;base64," + b64,
                                      inference_detail="auto")])
    if mode == "text":
        if not desc:
            return None
        return ("user", [f"[screen after: {label}]{trail} {desc}"])
    return None
```

- [ ] **Step 4: Run → PASS** (note: `decide_mode` auto tests depend on Task 4's `resolve_route_primary_model`; if running Task 3 before Task 4, the two `_FakeDispatch` auto tests error on import — that's expected; they pass after Task 4. Run the non-route tests now:)

Run: `.venv/bin/python -m pytest tests/test_computer_use_vision.py -q -k "build_injection or decide_mode_explicit or defaults_pixels"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/voice-agent/pipeline/computer_use_vision.py src/voice-agent/tests/test_computer_use_vision.py
git commit -m "feat(computer_use): vision mode decision + chat_ctx injection builder"
```

---

### Task 4: `resolve_route_primary_model` in `providers/llm.py`

**Files:** Modify `src/voice-agent/providers/llm.py`, `tests/test_computer_use_vision.py`.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_computer_use_vision.py`:

```python
def test_resolve_route_primary_model(monkeypatch):
    from providers.llm import resolve_route_primary_model
    monkeypatch.delenv("JARVIS_TASK_DESKTOP_MODEL", raising=False)
    monkeypatch.delenv("JARVIS_TASK_MODEL", raising=False)
    assert cuv.is_vision_capable(resolve_route_primary_model("TASK_DESKTOP")) is True   # claude default
    monkeypatch.setenv("JARVIS_TASK_DESKTOP_MODEL", "llama-3.3-70b-versatile")
    assert resolve_route_primary_model("TASK_DESKTOP") == "llama-3.3-70b-versatile"     # override wins
    assert resolve_route_primary_model("NOT_A_ROUTE") == ""                              # unknown → ""
```

- [ ] **Step 2: Run → FAIL**

Run: `.venv/bin/python -m pytest tests/test_computer_use_vision.py -q -k resolve_route`
Expected: FAIL with `ImportError: cannot import name 'resolve_route_primary_model'`.

- [ ] **Step 3: Implement** — add to `src/voice-agent/providers/llm.py` at module level (immediately after the `_ANTH_DEFAULT_PER_ROUTE = { ... }` block, ~line 944):

```python
def resolve_route_primary_model(route: str) -> str:
    """Public: resolve a route's PRIMARY supervisor model id (rung-1 only).

    Lookup order mirrors build_dispatching_llm's nested _resolve_route_model:
      1. per-route env override (JARVIS_TASK_DESKTOP_MODEL etc.)
      2. legacy JARVIS_TASK_MODEL for TASK_* routes
      3. specialty_routes spec default, else the _ANTH_DEFAULT_PER_ROUTE default.
    Returns '' for an unknown route. Used by the computer_use vision gate."""
    entry = _ANTH_DEFAULT_PER_ROUTE.get(route)
    if entry is None:
        return ""
    env_var, default_model, _temp = entry
    override = os.environ.get(env_var, "").strip()
    if override:
        return override
    legacy_task = os.environ.get("JARVIS_TASK_MODEL", "").strip()
    if legacy_task and route.startswith("TASK_"):
        return legacy_task
    try:
        spec_default = _specialty.get_primary_model(route)
    except Exception:
        spec_default = None
    return spec_default or default_model
```

- [ ] **Step 4: Run → PASS** (the whole vision module suite, including the Task 3 auto-route tests that needed this)

Run: `.venv/bin/python -m pytest tests/test_computer_use_vision.py -q`
Expected: PASS (all tests).

- [ ] **Step 5: Commit**

```bash
git add src/voice-agent/providers/llm.py src/voice-agent/tests/test_computer_use_vision.py
git commit -m "feat(llm): public resolve_route_primary_model for the vision gate"
```

---

### Task 5: wire `computer_use` to publish frames + record actions

**Files:** Modify `src/voice-agent/tools/computer_use.py`, `tests/test_computer_use_vision.py`.

- [ ] **Step 1: Write the failing test** — append to `tests/test_computer_use_vision.py`:

```python
def test_capture_response_publishes_frame():
    """tools.computer_use._capture_response should publish the frame to the cache."""
    import importlib
    import tools.computer_use as cu
    from tools.computer_use_backend import CaptureResult
    cuv.clear()
    cap = CaptureResult(mode="som", width=1920, height=1080, png_b64="ZZZZ")
    cu._capture_response(cap)                       # side effect: publish
    cur = cuv.take_current()
    assert cur is not None and cur["png_b64"] == "ZZZZ" and cur["width"] == 1920
```

(`CaptureResult` is a dataclass; `mode/width/height` are required positional/kw — adjust the constructor call if its signature differs. Read `tools/computer_use_backend.py` `class CaptureResult` first; it has fields `mode,width,height,png_b64,elements,app,window_title,png_bytes_len`.)

- [ ] **Step 2: Run → FAIL**

Run: `.venv/bin/python -m pytest tests/test_computer_use_vision.py -q -k capture_response_publishes`
Expected: FAIL (no publish yet → `take_current()` is None).

- [ ] **Step 3: Implement** — in `tools/computer_use.py`:

(a) Inside `_capture_response(cap)`, immediately before `return json.dumps(payload)`, add:

```python
    # Vision-feedback loop (P2a): publish the frame so JarvisAgent.llm_node can
    # inject it into the next generation. Best-effort — never break the tool.
    try:
        from pipeline import computer_use_vision
        computer_use_vision.publish_capture(
            png_b64=cap.png_b64, width=cap.width, height=cap.height,
            action_label=f"capture/{cap.mode}")
    except Exception:
        pass
```

(b) Inside `handle_computer_use(args, **kwargs)`, after the action is determined and dispatched (right before `return`), record the action label for the trail. Locate the existing `action = ...` and `_summarize_action(action, args)` usage; add after the dispatch result is computed:

```python
    try:
        from pipeline import computer_use_vision
        computer_use_vision.record_action(_summarize_action(action, args))
    except Exception:
        pass
```

(If `action`/`_summarize_action` aren't both in scope at the return point, record inside `_dispatch` instead, where `action` and `args` are parameters — read `handle_computer_use`/`_dispatch` first and place the one `record_action(...)` call where both are available, once per call.)

- [ ] **Step 4: Run → PASS**

Run: `.venv/bin/python -m pytest tests/test_computer_use_vision.py -q -k capture_response_publishes`
Expected: PASS. Also `.venv/bin/python -c "import sys; sys.path.insert(0,'.'); import tools.computer_use"` → clean (no new import-time deps).

- [ ] **Step 5: Commit**

```bash
git add src/voice-agent/tools/computer_use.py src/voice-agent/tests/test_computer_use_vision.py
git commit -m "feat(computer_use): publish capture frame + record action for the vision loop"
```

---

### Task 6: `JarvisAgent.llm_node` injection + cache clear

**Files:** Modify `src/voice-agent/jarvis_agent.py`.

No new unit test (the async-generator `llm_node` wrapper is exercised by Task 3's `build_injection`/`decide_mode` tests + the live acceptance in Task 7). This task wires them in.

- [ ] **Step 1: Add the `llm_node` override** — in `class JarvisAgent(Agent)` (jarvis_agent.py:3797), add a method (place it near `on_user_turn_completed`):

```python
    async def llm_node(self, chat_ctx, tools, model_settings):
        """Vision-feedback loop (P2a): before generating, inject the post-action
        screen (pixels for a vision-capable model, else a text description) into
        THIS generation's chat_ctx copy. Ephemeral — never persists to history.
        Best-effort: any failure just skips injection and generates normally."""
        try:
            from pipeline import computer_use_vision as _cuv
            cap = _cuv.take_current()
            if cap is not None:
                mode = _cuv.decide_mode(getattr(self, "_dispatch_llm", None))
                desc = None
                if mode == "text":
                    try:
                        from pipeline.screen_share_observer import latest_description_global
                        desc = latest_description_global()
                    except Exception:
                        desc = None
                inj = _cuv.build_injection(cap=cap, mode=mode, desc=desc)
                if inj is not None:
                    role, content = inj
                    chat_ctx.add_message(role=role, content=content)
        except Exception:
            logger.debug("[vision] injection skipped", exc_info=True)
        async for chunk in Agent.default.llm_node(self, chat_ctx, tools, model_settings):
            yield chunk
```

- [ ] **Step 2: Stash the dispatch LLM on the agent** — in `entrypoint()`, immediately after `_jarvis_agent = JarvisAgent(...)` (jarvis_agent.py:6625), add:

```python
    # Give llm_node a handle to the per-route DispatchingLLM for the vision gate's
    # best-effort active-model detection (defaults to pixels if absent).
    try:
        _jarvis_agent._dispatch_llm = _dispatch_llm
    except Exception:
        pass
```

- [ ] **Step 3: Clear the cache on a new user turn** — in `JarvisAgent.on_user_turn_completed` (jarvis_agent.py:3834), add near the top of the method (after the docstring, before the `_cua_confirm_future` block is fine — it must run on every user turn):

```python
        try:
            from pipeline import computer_use_vision
            computer_use_vision.clear()
        except Exception:
            pass
```

- [ ] **Step 4: Verify it imports + compiles**

Run: `cd src/voice-agent && .venv/bin/python -m py_compile jarvis_agent.py && .venv/bin/python -c "import jarvis_agent; print('import ok')"`
Expected: `import ok` (no syntax/import error; the override is defined, not yet exercised).

- [ ] **Step 5: Commit**

```bash
git add src/voice-agent/jarvis_agent.py
git commit -m "feat(computer_use): JarvisAgent.llm_node injects post-action screen (vision loop)"
```

---

### Task 7: Integration + verification

**Files:** none (verification only).

- [ ] **7.1** Full vision suite: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_computer_use_vision.py -q` → all PASS.
- [ ] **7.2** Full suite (regression): `.venv/bin/python -m pytest tests/ -q` → green (no regressions; expect ~2809+ passed).
- [ ] **7.3** Compile + import: `.venv/bin/python -m py_compile jarvis_agent.py tools/computer_use.py providers/llm.py pipeline/computer_use_vision.py` and `.venv/bin/python -c "import jarvis_agent; print('ok')"` → ok. Confirm `computer_use` schema unchanged: `git diff --stat HEAD~6 -- tools/computer_use.py` shows only the two best-effort insertions, no `COMPUTER_USE_SCHEMA` edits.
- [ ] **7.4 Live acceptance (manual, needs a running session; like P1):**
  - With the default supervisor (Claude), have JARVIS do a desktop task that captures (e.g. "open the settings and tell me what's on screen"): confirm via `~/.local/share/jarvis/logs/voice-agent.log` that a generation following a `computer_use` capture references actual on-screen content, and (optional) add a temporary debug log in `llm_node` to confirm `mode=pixels` + an `ImageContent` was added.
  - Flip `JARVIS_CU_VISION_MODE=text` (service env): confirm the description path is used, no image, no error.
  - Flip `JARVIS_CU_VISION_MODE=off`: confirm no injection.
  - Restart safety: check `turn_telemetry.db` latest `ts_utc` is >60s old before restarting the service to pick up env changes (CLAUDE.md rule).
- [ ] **7.5** End-of-task summary (CHANGED / NOT CHANGED / VERIFY), confirming the OUT list (`dispatch_agent`/`background_tasks` WIP, `computer_use` schema) untouched.

**Acceptance:** all `test_computer_use_vision` pass; full suite green; `import jarvis_agent` clean; with a vision-capable supervisor a post-`computer_use` generation perceives the actual screen (pixels) and `JARVIS_CU_VISION_MODE=text`/`off` behave correctly; no `computer_use` schema change; injection is ephemeral (no growth in canonical history).

---

## Self-review
- **Spec coverage:** Decision 1 (ImageContent injection) → Tasks 3+6; Decision 2 (llm_node, ephemeral) → Task 6; Decision 3 (capture cache, pure tool) → Tasks 1+5; Decision 4 (JARVIS_CU_VISION_MODE gate + resolver) → Tasks 3+4; Decision 5 (newest-only, downscale, trail) → Tasks 1+2. Testing → Tasks 1-5 + 7. All covered.
- **Placeholders:** none — every code step is complete. The only "read the file first" notes (CaptureResult ctor in Task 5, action label placement) are real codebase-fit checks, not deferred logic.
- **Consistency:** `publish_capture`/`record_action`/`take_current`/`clear`/`recent_actions_text`/`is_vision_capable`/`downscale_png`/`decide_mode`/`build_injection` names are used identically across Tasks 1-6; `resolve_route_primary_model` signature matches its caller in `decide_mode`; `Agent.default.llm_node` delegation matches the verified pattern.
