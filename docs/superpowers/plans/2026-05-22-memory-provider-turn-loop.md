# MemoryProvider-Driven Turn-Loop Memory — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a pluggable cloud `MemoryProvider` layer (Honcho first) that drives cross-session recall + background writes, augmenting — never replacing — JARVIS's file-backed memory. Off by default.

**Architecture:** A new `pipeline/memory_provider.py` runtime resolves the active backend (`JARVIS_MEMORY_PROVIDER` env) from `_provider_registry` (kind=`memory`), owns the per-session lifecycle, fires fire-and-forget background sync, and serves gated auto-recall. A `recall()` registry tool exposes deep dialectic lookups. Wired into `JarvisAgent` via `on_enter` (begin session), `conversation_item_added` (sync each user/assistant item), `on_user_turn_completed` (gated auto-recall before the LLM), `on_exit` (end session). Honcho implements the interface via the high-level `honcho-ai` `AsyncHoncho` SDK. `pipeline/file_memory.py` + the `memory()` tool + the frozen snapshot are untouched.

**Tech Stack:** Python 3.13, LiveKit Agents 1.5.9 (`Agent` lifecycle hooks + `ChatContext`), `honcho-ai` SDK, `tools/_provider_registry.py`, `pipeline/turn_router.is_recall_query`.

**Spec:** `docs/superpowers/specs/2026-05-22-memory-provider-turn-loop-design.md`

**Hard invariants (hold after every task):**
- Off by default: with `JARVIS_MEMORY_PROVIDER` unset, NOTHING changes — no recall tool in the surface, no sync, no auto-recall, no latency. Verify in tests.
- Never blocks TTS: dialectic only behind the explicit tool; auto-recall uses the cheap path + a hard timeout; sync is fire-and-forget.
- `import jarvis_agent` clean; full suite green; `tests/test_no_duplicate_tools.py` green; file-memory tests untouched.
- The conftest provider-registry isolation fixture (added 2026-05-22) restores the registry between tests — rely on it; do NOT add `reset_providers()` calls that wipe built-ins.

**Out of scope:** mem0 + the other 6 memory backends stay inert (interface is built to fit mem0 next); replacing file-memory; migrating MEMORY.md/USER.md content.

---

## Task 1: Real `MemoryProvider` base methods + `recall` tool gate

Currently `tools/memory_providers.py` has a lean base with safe-default no-ops (`prefetch`/`sync_turn`/`system_prompt_block` from the earlier port). Replace those with the spec's interface and add the runtime-facing gate. NO turn-loop wiring yet.

**Files:**
- Modify: `src/voice-agent/tools/memory_providers.py`
- Test: `src/voice-agent/tests/test_memory_providers.py` (extend existing)

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_memory_providers.py
def test_base_interface_safe_defaults():
    from tools.memory_providers import MemoryProvider

    class P(MemoryProvider):
        name = "p"
        def is_available(self): return True

    p = P()
    assert p.recall("anything") == ""
    assert p.recall_context("x") == ""
    p.initialize("sess")          # no raise
    p.sync_message("user", "hi")  # no raise
    p.end_session()               # no raise


def test_active_provider_none_when_flag_unset(monkeypatch):
    monkeypatch.delenv("JARVIS_MEMORY_PROVIDER", raising=False)
    from tools.memory_providers import active_provider_name
    assert active_provider_name() is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_memory_providers.py -q`
Expected: FAIL (`recall`/`recall_context`/`sync_message`/`active_provider_name` not defined).

- [ ] **Step 3: Update the base in `tools/memory_providers.py`**

Replace the existing lean base body with:

```python
import abc
import os
from typing import Any, Optional

PROVIDER_KIND = "memory"


class MemoryProvider(abc.ABC):
    """Cloud memory backend. Duck-typed for the provider registry.

    recall/recall_context take a natural-language string and return an opaque
    text block (Honcho returns prose; mem0 concatenates rows). All methods have
    safe defaults so a partial backend never breaks a turn.
    """
    name: str = ""

    @abc.abstractmethod
    def is_available(self) -> bool: ...

    def initialize(self, session_id: str) -> None:
        return None

    def recall(self, query: str) -> str:
        """Deep recall (e.g. Honcho dialectic peer.chat). NL-in, text-out."""
        return ""

    def recall_context(self, hint: str = "") -> str:
        """Cheap recent-context recall (e.g. Honcho session.get_context)."""
        return ""

    def sync_message(self, role: str, text: str) -> None:
        """Ingest one message (role: 'user'|'assistant'). Background-called."""
        return None

    def end_session(self) -> None:
        return None


def active_provider_name() -> Optional[str]:
    """The backend named by JARVIS_MEMORY_PROVIDER, or None (layer off)."""
    name = os.environ.get("JARVIS_MEMORY_PROVIDER", "").strip()
    return name or None
```

(Keep `memory_bridge_enabled()` if present, or remove it — `active_provider_name()` supersedes it. Update any test referencing `memory_bridge_enabled` to `active_provider_name`.)

- [ ] **Step 4: Run to verify pass**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_memory_providers.py tests/test_no_duplicate_tools.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd src/voice-agent && git add tools/memory_providers.py tests/test_memory_providers.py
git commit -m "feat(memory): real MemoryProvider interface (recall/recall_context/sync_message) + active_provider_name"
```

---

## Task 2: `pipeline/memory_provider.py` runtime

The single owner of resolution + lifecycle + async sync + gated recall. All entry points no-op when no provider is active or available.

**Files:**
- Create: `src/voice-agent/pipeline/memory_provider.py`
- Test: `src/voice-agent/tests/test_memory_provider_runtime.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_memory_provider_runtime.py
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


class _Fake:
    name = "fake"
    def __init__(self): self.calls = []
    def is_available(self): return True
    def initialize(self, sid): self.calls.append(("init", sid))
    def recall_context(self, hint=""): self.calls.append(("ctx", hint)); return "CTX"
    def recall(self, q): self.calls.append(("recall", q)); return "DEEP"
    def sync_message(self, role, text): self.calls.append(("sync", role, text))
    def end_session(self): self.calls.append(("end",))


def _install(monkeypatch, prov):
    from tools import _provider_registry as pr
    pr.reset_providers("memory")
    pr.register_provider("memory", prov.name, prov)
    monkeypatch.setenv("JARVIS_MEMORY_PROVIDER", prov.name)


def test_runtime_noop_when_flag_unset(monkeypatch):
    monkeypatch.delenv("JARVIS_MEMORY_PROVIDER", raising=False)
    from pipeline import memory_provider as mp
    assert mp.active_provider() is None
    mp.begin_session("room1")          # no raise
    assert mp.recall_for_query("hi") == ""


def test_begin_session_and_recall(monkeypatch):
    from pipeline import memory_provider as mp
    f = _Fake(); _install(monkeypatch, f)
    mp.begin_session("room1")
    assert ("init", "room1") in f.calls
    assert mp.recall_for_query("deep q") == "DEEP"


def test_sync_async_is_fire_and_forget(monkeypatch):
    from pipeline import memory_provider as mp
    f = _Fake(); _install(monkeypatch, f)
    async def run():
        mp.begin_session("r")
        mp.sync_item_async("user", "hello")
        await asyncio.sleep(0.05)  # let the task run
    asyncio.run(run())
    assert ("sync", "user", "hello") in f.calls


def test_sync_swallows_provider_error(monkeypatch):
    from pipeline import memory_provider as mp
    class Boom(_Fake):
        def sync_message(self, role, text): raise RuntimeError("boom")
    b = Boom(); _install(monkeypatch, b)
    async def run():
        mp.begin_session("r")
        mp.sync_item_async("user", "x")
        await asyncio.sleep(0.05)   # must NOT raise out
    asyncio.run(run())  # no exception escapes
```

- [ ] **Step 2: Run to verify it fails** — `pytest tests/test_memory_provider_runtime.py -q` → FAIL (module missing).

- [ ] **Step 3: Implement `pipeline/memory_provider.py`**

```python
"""Runtime owner of the cloud MemoryProvider layer (augments file-memory).

Resolves the backend named by JARVIS_MEMORY_PROVIDER from the provider registry
(kind="memory"), owns the per-session lifecycle, fires fire-and-forget background
sync, and serves recall. Every entry point is a safe no-op when no provider is
active/available, so the whole layer is inert by default. Never blocks the voice
turn: sync is fire-and-forget; deep recall is invoked only by the recall() tool.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
from typing import Any, Optional

from tools import _provider_registry as provider_registry
from tools.memory_providers import PROVIDER_KIND, active_provider_name

logger = logging.getLogger("jarvis.memory_provider")

_session_started = False


def active_provider() -> Optional[Any]:
    """The configured + available memory provider, or None."""
    name = active_provider_name()
    if not name:
        return None
    prov = provider_registry.get_provider(PROVIDER_KIND, name)
    if prov is None:
        return None
    try:
        if not prov.is_available():
            return None
    except Exception:  # noqa: BLE001
        return None
    return prov


def begin_session(session_id: str) -> None:
    global _session_started
    prov = active_provider()
    if prov is None:
        return
    try:
        prov.initialize(session_id)
        _session_started = True
        logger.info("memory provider %r session begun (%s)", prov.name, session_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("memory provider begin_session failed: %s", exc)


def end_session() -> None:
    global _session_started
    prov = active_provider()
    if prov is None or not _session_started:
        return
    try:
        prov.end_session()
    except Exception as exc:  # noqa: BLE001
        logger.warning("memory provider end_session failed: %s", exc)
    finally:
        _session_started = False


def _run_maybe_async(fn, *args):
    """Call a possibly-async provider method to completion (background context)."""
    if inspect.iscoroutinefunction(fn):
        return fn(*args)  # returns a coroutine the caller awaits
    return fn(*args)


def sync_item_async(role: str, text: str) -> None:
    """Fire-and-forget background sync of one conversation item. Never raises."""
    prov = active_provider()
    if prov is None or not (text or "").strip():
        return

    async def _task():
        try:
            res = prov.sync_message(role, text)
            if inspect.isawaitable(res):
                await res
        except Exception as exc:  # noqa: BLE001 — background, must not surface
            logger.debug("memory sync_message failed (%s): %s", role, exc)

    try:
        asyncio.get_running_loop().create_task(_task())
    except RuntimeError:
        # No running loop (sync context / tests) — run inline best-effort.
        try:
            prov.sync_message(role, text)
        except Exception as exc:  # noqa: BLE001
            logger.debug("memory sync_message (inline) failed: %s", exc)


def recall_for_query(query: str) -> str:
    """Deep recall via the active provider (used by the recall() tool)."""
    prov = active_provider()
    if prov is None:
        return ""
    try:
        res = prov.recall(query)
        return res if isinstance(res, str) else ""
    except Exception as exc:  # noqa: BLE001
        logger.warning("memory recall failed: %s", exc)
        return ""


async def maybe_recall_for_turn(text: str, *, timeout_s: float = 1.5) -> str:
    """Cheap gated auto-recall for on_user_turn_completed. Returns "" on any
    miss/timeout/error so the turn always proceeds. Caller decides whether the
    turn is recall-ish (via turn_router.is_recall_query)."""
    prov = active_provider()
    if prov is None:
        return ""

    async def _call() -> str:
        res = prov.recall_context(text)
        if inspect.isawaitable(res):
            res = await res
        return res if isinstance(res, str) else ""

    try:
        return await asyncio.wait_for(asyncio.to_thread(lambda: asyncio.run(_call()))
                                      if not _is_async_provider(prov) else _call(),
                                      timeout=timeout_s)
    except Exception as exc:  # noqa: BLE001 — timeout or provider error → no inject
        logger.debug("auto-recall skipped (%s)", exc)
        return ""


def _is_async_provider(prov) -> bool:
    return inspect.iscoroutinefunction(getattr(prov, "recall_context", None))
```

NOTE: the `maybe_recall_for_turn` async/sync bridging is fiddly — simplify during implementation to match whether the chosen Honcho method is sync or async (Honcho `AsyncHoncho` is async → the `_call()` path; a sync provider → `asyncio.to_thread(prov.recall_context, text)`). Keep the timeout + swallow-all contract. Add a unit test for the timeout path with a slow fake.

- [ ] **Step 4: Run to verify pass** — `pytest tests/test_memory_provider_runtime.py tests/test_no_duplicate_tools.py -q` → PASS.

- [ ] **Step 5: Commit** — `git add pipeline/memory_provider.py tests/test_memory_provider_runtime.py && git commit -m "feat(memory): provider runtime — session lifecycle, fire-and-forget sync, gated recall"`

---

## Task 3: `recall` registry tool

**Files:**
- Modify: `src/voice-agent/tools/memory_providers.py` (register the tool at import, like `web_providers.py`)
- Test: `src/voice-agent/tests/test_memory_recall_tool.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_memory_recall_tool.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))


def test_recall_tool_registered():
    import tools.memory_providers  # noqa: F401
    from tools.registry import registry
    assert "recall" in set(registry.all_names())


def test_recall_tool_inert_without_provider(monkeypatch):
    monkeypatch.delenv("JARVIS_MEMORY_PROVIDER", raising=False)
    from tools.memory_providers import check_recall_available
    assert check_recall_available() is False
```

- [ ] **Step 2: Run to verify it fails** — FAIL.

- [ ] **Step 3: Add the tool to `tools/memory_providers.py`**

```python
def check_recall_available() -> bool:
    """check_fn: a memory provider is configured + available."""
    from pipeline import memory_provider  # lazy (avoid import cycle at module load)
    return memory_provider.active_provider() is not None


async def _handle_recall(args: dict) -> str:
    query = (args.get("query") or "").strip() if isinstance(args, dict) else ""
    if not query:
        from tools.registry import tool_error
        return tool_error("recall requires a 'query' (what to look up about the user/past).")
    from pipeline import memory_provider
    import asyncio
    res = await asyncio.to_thread(memory_provider.recall_for_query, query)
    return res or "No relevant memory found."


_RECALL_SCHEMA = {
    "name": "recall",
    "description": (
        "Look up what you know about the user from past conversations (cross-session "
        "memory). Use for 'what did I tell you about X', 'remember when…', or when you "
        "need durable context the current chat doesn't contain. Returns a synthesized "
        "answer; may take a moment. For facts in the current chat, just answer directly."
    ),
    "parameters": {
        "type": "object",
        "properties": {"query": {"type": "string",
            "description": "Natural-language question about the user or past context."}},
        "required": ["query"],
    },
}

from tools.registry import registry as _registry  # if not already imported
_registry.register(
    name="recall", schema=_RECALL_SCHEMA, handler=_handle_recall, toolset="memory",
    check_fn=check_recall_available, requires_env=["JARVIS_MEMORY_PROVIDER"],
    is_async=True, emoji="🧠", max_result_size_chars=8_000,
)
```

(Guard against a circular import: `tools.memory_providers` must not import `pipeline.memory_provider` at module top — use the lazy imports shown.)

- [ ] **Step 4: Run** — `pytest tests/test_memory_recall_tool.py tests/test_no_duplicate_tools.py -q` → PASS (recall registered; inert without flag).

- [ ] **Step 5: Commit** — `git add tools/memory_providers.py tests/test_memory_recall_tool.py && git commit -m "feat(memory): recall() registry tool (inert until JARVIS_MEMORY_PROVIDER set)"`

---

## Task 4: Honcho real implementation

**Files:**
- Modify: `src/voice-agent/plugins/memory/honcho/__init__.py` (replace the lean stub with a real impl)
- Modify: `src/voice-agent/requirements.txt` (add `honcho-ai`)
- Test: `src/voice-agent/tests/test_honcho_provider.py`

- [ ] **Step 1: Read the Honcho SDK shape** — `pip show honcho-ai` after install; confirm `from honcho import AsyncHoncho` (high-level, NOT `honcho_core`), `honcho.peer(id)`, `honcho.session(id)`, `session.add_messages([...])`, `peer.chat(query)`, `session.get_context(summary=True)`. (See spec sources.) If the live SDK surface differs, adapt — keep the JARVIS interface stable.

- [ ] **Step 2: Write the failing test (gating + structure, no network)**

```python
# tests/test_honcho_provider.py
import importlib.util, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

def _load():
    spec = importlib.util.spec_from_file_location(
        "_t_honcho", Path(__file__).parent.parent / "plugins/memory/honcho/__init__.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m

def test_honcho_unavailable_without_key(monkeypatch):
    monkeypatch.delenv("HONCHO_API_KEY", raising=False)
    p = _load().HonchoMemoryProvider()
    assert p.name == "honcho"
    assert p.is_available() is False

def test_honcho_recall_safe_when_uninitialized(monkeypatch):
    monkeypatch.delenv("HONCHO_API_KEY", raising=False)
    p = _load().HonchoMemoryProvider()
    # No session/key → returns "" rather than raising.
    assert p.recall("anything") == ""
    assert p.recall_context("x") == ""
```

- [ ] **Step 3: Run to verify it fails** — FAIL.

- [ ] **Step 4: Implement** `plugins/memory/honcho/__init__.py` subclassing `tools.memory_providers.MemoryProvider`:
  - `name="honcho"`; `is_available()` → `HONCHO_API_KEY` set AND `honcho` importable.
  - `initialize(session_id)` → lazily build `AsyncHoncho(api_key=...)`, `self._peer_user = honcho.peer("ulrich")`, `self._peer_agent = honcho.peer("jarvis")`, `self._session = honcho.session(session_id)`; store the event loop / handles. All guarded — on failure, set handles None and log (recall/sync then no-op).
  - `sync_message(role, text)` → `await self._session.add_messages([(self._peer_user if role=="user" else self._peer_agent).message(text)])`. Async (matches `AsyncHoncho`); the runtime awaits it.
  - `recall(query)` → `await self._peer_user.chat(query)` (dialectic); return the prose string.
  - `recall_context(hint)` → `ctx = await self._session.get_context(summary=True, tokens=512)`; return a compact text rendering (e.g. `str(ctx)` or `ctx.to_anthropic()` text). Cheap path.
  - `end_session()` → best-effort flush/close.
  - Every method wrapped so a missing key / uninitialized handle / SDK error returns `""` / no-op, never raises. ZERO `hermes` tokens.
  - Add `honcho-ai` to `requirements.txt` (a comment noting the layer is inert without it + the key).

- [ ] **Step 5: Run** — `pytest tests/test_honcho_provider.py tests/test_no_duplicate_tools.py -q` → PASS. (Live recall/sync untested without a key — note in the commit.)

- [ ] **Step 6: Commit** — `git add plugins/memory/honcho/__init__.py requirements.txt tests/test_honcho_provider.py && git commit -m "feat(memory): real Honcho backend (AsyncHoncho dialectic recall + add_messages sync); inert without key"`

---

## Task 5: Wire into `JarvisAgent` (additive)

**Files:**
- Modify: `src/voice-agent/jarvis_agent.py`
- Test: `src/voice-agent/tests/test_memory_turn_integration.py`

- [ ] **Step 1: Read the integration points first.** Read `jarvis_agent.py` around: the `JarvisAgent` class (3537+) for existing `on_enter`/`on_exit` (add if absent); the `on_user_turn_completed` body (3548–~3700) to find where `text` is finalized and the turn proceeds to the LLM (after the garbage/echo/silent gates, ~line 3656+); and the `conversation_item_added` handler (~5091) for its current shape. Integrate ADDITIVELY — do not disturb existing gates/handlers.

- [ ] **Step 2: Write the failing test (integration via monkeypatched runtime)**

```python
# tests/test_memory_turn_integration.py — verifies the hooks call the runtime,
# gated by is_recall_query, without disturbing existing turn handling.
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

def test_auto_recall_only_on_recall_queries(monkeypatch):
    import jarvis_agent
    calls = []
    monkeypatch.setattr(jarvis_agent, "_memory_auto_recall_text",
                        lambda text: calls.append(text) or "")  # see Step 3 helper
    from pipeline import turn_router
    assert turn_router.is_recall_query("what did I tell you about my dog") is True
    assert turn_router.is_recall_query("set a timer for 5 minutes") is False
```

(Exact assertions depend on the helper seam you introduce in Step 3 — keep the test focused on "auto-recall is attempted only when is_recall_query is true" and "sync is called per conversation item". Use a fake provider installed in the registry + `JARVIS_MEMORY_PROVIDER=fake`.)

- [ ] **Step 3: Add the hooks.**
  - **`on_enter`** (add to `JarvisAgent`): `from pipeline import memory_provider; memory_provider.begin_session(<room/session id>)`. (Derive the id from `self.session` / room — match how other session ids are obtained in the file.) Call `super().on_enter()` if the base defines it.
  - **`on_exit`**: `memory_provider.end_session()` (+ `super().on_exit()`).
  - **`on_user_turn_completed`** — after the existing garbage/echo/silent gates pass and `text` is the clean transcript (insert BEFORE the method falls through to normal LLM dispatch, ~after line 3656), add:
    ```python
    # Gated cross-session auto-recall (cheap path; never blocks — see memory_provider).
    try:
        from pipeline import turn_router, memory_provider
        if memory_provider.active_provider() is not None and turn_router.is_recall_query(text):
            ctx = await memory_provider.maybe_recall_for_turn(text)
            if ctx:
                turn_ctx.add_message(role="assistant", content=f"[memory] {ctx}")
    except Exception as e:  # noqa: BLE001 — memory must never break a turn
        logger.debug(f"[memory] auto-recall skipped: {e}")
    ```
  - **`conversation_item_added`** (the session handler ~5091) — add a fire-and-forget sync of each item:
    ```python
    # Background sync to the cloud memory provider (no-op when layer off).
    try:
        from pipeline import memory_provider
        role = getattr(item, "role", "") or ""
        item_text = ""
        try: item_text = item.text_content() or ""
        except Exception: item_text = getattr(item, "content", "") if isinstance(getattr(item, "content", ""), str) else ""
        if role in ("user", "assistant") and item_text.strip():
            memory_provider.sync_item_async(role, item_text)
    except Exception:
        pass
    ```
  (Match the actual `item` shape in that handler — it already extracts text for other purposes; reuse that extraction.)

- [ ] **Step 4: Run** — `pytest tests/test_memory_turn_integration.py tests/test_no_duplicate_tools.py -q && .venv/bin/python -c "import jarvis_agent"` → PASS + clean import.

- [ ] **Step 5: Commit** — `git add jarvis_agent.py tests/test_memory_turn_integration.py && git commit -m "feat(memory): wire provider into JarvisAgent (begin/end session, per-item sync, gated auto-recall)"`

---

## Task 6: Final verification

- [ ] **Step 1: Full suite** — `cd src/voice-agent && .venv/bin/python -m pytest tests/ -q` → all green (no regressions vs the 2051 baseline + the new tests).
- [ ] **Step 2: Off-by-default proof** — `.venv/bin/python -c "import os; os.environ.pop('JARVIS_MEMORY_PROVIDER',None); from tools._adapter import load_all_livekit_tools; n=[t.info.name for t in load_all_livekit_tools()]; print('recall present:', 'recall' in n)"` → `recall present: False` (tool filtered out when off).
- [ ] **Step 3: No-dup + import** — `pytest tests/test_no_duplicate_tools.py -q` PASS; `.venv/bin/python -c "import jarvis_agent"` clean.
- [ ] **Step 4: file-memory untouched** — `git diff --stat HEAD~6 -- pipeline/file_memory.py tools/memory.py` → empty (no changes to the curated layer).
- [ ] **Step 5: hermes-token scan** — `! grep -rinE 'hermes' pipeline/memory_provider.py tools/memory_providers.py plugins/memory/honcho/` → no matches.
- [ ] **Step 6: Restart decision** — check `~/.local/share/jarvis/turn_telemetry.db` latest `ts_utc`; if >60s idle, restart `jarvis-voice-agent.service` and confirm clean boot. Layer stays inert (no `JARVIS_MEMORY_PROVIDER`) — to exercise live: `pip install honcho-ai` + set `HONCHO_API_KEY` + `JARVIS_MEMORY_PROVIDER=honcho`, then two-session recall test.

---

## Self-review notes

- **Spec coverage:** interface (T1), runtime (T2), recall tool (T3), Honcho impl + dep (T4), turn-loop hooks (T5), verification + off-by-default + file-memory-untouched (T6). All spec sections covered.
- **Off-by-default:** every runtime entry point checks `active_provider()`; recall tool `check_fn` gates on it; T6 Step 2 proves the surface is unchanged when off.
- **Latency/safety:** sync fire-and-forget (T2); auto-recall timeout + swallow-all (T2); dialectic only in the tool (T3); each hook wrapped so memory never breaks a turn (T5).
- **Type consistency:** `recall`/`recall_context`/`sync_message`/`initialize`/`end_session` used identically across T1 base, T2 runtime, T4 Honcho, T5 hooks. Runtime fns: `active_provider`/`begin_session`/`end_session`/`sync_item_async`/`recall_for_query`/`maybe_recall_for_turn`.
- **Known fiddly bit:** `maybe_recall_for_turn`'s sync/async bridging (T2 Step 3 note) — simplify to match Honcho's async surface during implementation; the contract (timeout + return "" on miss) is what matters.
