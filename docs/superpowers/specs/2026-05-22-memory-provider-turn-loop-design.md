# MemoryProvider-Driven Turn-Loop Memory — Design

**Date:** 2026-05-22
**Status:** Design (awaiting implementation plan)
**Branch context:** follows the 2026-05-22 Hermes plugin port (`feat/voice-skill-loop`), which copied the `memory` plugin family present-but-inert. This spec makes a cloud memory backend (Honcho first) actually drive recall + writes.

## Goal

Give the JARVIS voice agent a dynamic, cross-session memory layer powered by a pluggable cloud `MemoryProvider` (Honcho first, mem0 next), **augmenting** — not replacing — the existing file-backed curated memory. The provider models the user from conversation and answers recall queries; file-memory keeps owning curated, in-prompt, deliberately-written facts.

## Motivation

The Hermes plugin port copied 8 `memory` backends and wired `register_memory_provider` into the provider registry, but the backends are inert: JARVIS's turn loop uses file-backed memory (`pipeline/file_memory.py`, MEMORY.md/USER.md) and has **no consumer** for a `MemoryProvider`. This spec adds that consumer. The user authorized restructuring JARVIS memory to make this work; the design below does it additively (file-memory untouched) so the cloud provider is never a hard dependency.

## Design decisions (settled during brainstorming + online research)

| Decision | Choice | Rationale |
|---|---|---|
| Replace vs augment | **Augment** — two layers | Preserves the anti-gaslighting / anti-garbage / prefix-cache guarantees of file-memory; provider is never a hard dependency (works offline via file-memory). |
| Recall delivery | **Gated auto-recall + explicit tool** | Pure pull-only feels forgetful (LLMs under-call recall tools — research). Auto-recall fires only on recall-ish turns via the existing `turn_router.is_recall_query`; the explicit `recall()` tool covers deeper lookups. |
| Write trigger | **Background async auto-sync** of turns | Honcho/mem0 derive facts server-side; pull-only recall means server modeling never auto-pollutes the prompt (the 22%-garbage failure was auto-*injected* paraphrases). Async = no added turn latency. |
| Recall method contract | **`recall(query: str) -> str`** (NL-in, opaque-text-out) | Honcho recall is `peer.chat()` (dialectic NL reasoning returning prose), NOT keyword search. mem0 is `search()`. One interface fits both only as NL-query-in / text-block-out. |
| Latency | Dialectic **never on the synchronous voice path** | `peer.chat()` is a multi-second server-side call. Keep it behind the explicit tool (opt-in); use the cheap `session.get_context()` for gated auto-recall, with a hard timeout. |
| Activation | **Off by default** | `JARVIS_MEMORY_PROVIDER=<name>` + that backend's key both required → zero behavior change otherwise (mirrors the existing gate). |
| First backend | **Honcho** | Flagship (dialectic Q&A + automatic user modeling). Abstraction must also fit mem0. |

## Architecture

```
┌─ jarvis_agent.py :: JarvisAgent(Agent) + session hooks ─────────────────────┐
│  on_enter                  → memory_provider.begin_session(room_id)          │
│  conversation_item_added   → memory_provider.sync_item_async(role, text)     │  fire-and-forget, per item
│      (session hook @5091)       (captures BOTH user + assistant items — the   │  (right place for writes:
│                                  natural fit for per-message peer modeling)   │   on_user_turn_completed has
│  on_user_turn_completed    → if is_recall_query(user):                       │   no assistant reply yet)
│      (overridden @3548)          inject recall_context(user) into turn_ctx    │  cheap path, tail-injected
│  on_exit                   → memory_provider.end_session()                    │  best-effort
└──────────────────────────────────────────────────────────────────────────────┘

(Writes use `conversation_item_added`, not `on_user_turn_completed`, because the
latter fires before the LLM reply exists. Honcho's `add_messages` is per-message
with peer attribution, so syncing each item as it lands is the natural fit.)
        │ resolves active backend by name (JARVIS_MEMORY_PROVIDER)
        ▼
   pipeline/memory_provider.py   (NEW runtime: session lifecycle, async sync queue,
        │                         gated-recall helper, active-provider resolution)
        │ reads from
   tools/_provider_registry.py   kind="memory"  ← plugins/memory/<backend>/ (registered)
        │
   tools/memory_providers.py     MemoryProvider base — REAL methods (was safe-default no-ops):
        │                         initialize / recall / recall_context / sync_turn / end_session
        ▼
   plugins/memory/honcho/__init__.py   Honcho impl via honcho-ai high-level SDK (AsyncHoncho)

   recall() registry tool  → active provider.recall(query)  (deep/dialectic; supervisor-invoked)

   pipeline/file_memory.py + memory() tool + frozen snapshot   ← UNTOUCHED (curated layer)
```

### Components

1. **`pipeline/memory_provider.py`** (new runtime) — single owner of: active-provider resolution (`JARVIS_MEMORY_PROVIDER` names it; resolve via `_provider_registry.get_provider("memory", name)`), per-session lifecycle (`begin_session`/`end_session`), the fire-and-forget sync task (`sync_turn_async`), and the gated-recall helper (`maybe_recall_for_turn(text) -> str`). All entry points are no-ops when no provider is active. Holds the current session handle.
2. **`recall` registry tool** — `recall(query: str)` → active provider `.recall(query)` (deep dialectic) → formatted tool result. `check_fn`: a memory provider is active **and** available → else filtered out (inert). Description nudges the supervisor on *when* to use it (deep/explicit user-history lookups).
3. **`tools/memory_providers.py`** — the `MemoryProvider` base gains real method signatures (currently safe-default no-ops): `initialize(session_id)`, `recall(query) -> str`, `recall_context(hint) -> str`, `sync_turn(user, assistant)`, `end_session()`. Defaults return `""` / no-op so a partial backend never breaks a turn.
4. **`plugins/memory/honcho/__init__.py`** — real implementation via the **high-level** `honcho-ai` SDK (`from honcho import AsyncHoncho`; NOT `honcho_core`): `begin_session`→`honcho.peer(user)` + `honcho.session(room_id)`; `sync_turn`→`session.add_messages([...])`; `recall`→`peer.chat(query)` (dialectic); `recall_context`→`session.get_context(summary=True, tokens=N)`. `is_available()` gates on `HONCHO_API_KEY` + `honcho` importable (already does).
5. **file-memory + `memory()` tool + frozen snapshot** — unchanged. The curated/in-prompt layer.

## Provider interface contract

```python
class MemoryProvider(abc.ABC):
    name: str
    @abc.abstractmethod
    def is_available(self) -> bool: ...
    def initialize(self, session_id: str) -> None: ...          # default: no-op
    def recall(self, query: str) -> str: return ""              # deep/dialectic; NL-in, text-out
    def recall_context(self, hint: str = "") -> str: return ""  # cheap recent-context
    def sync_message(self, role: str, text: str) -> None: ...   # default: no-op (role: "user"|"assistant")
    def end_session(self) -> None: ...                          # default: no-op
```

Contract: `recall`/`recall_context` take a natural-language string and return an opaque text block destined for the prompt or a tool result (Honcho returns prose; mem0 concatenates rows). Implementations may be sync or async; the runtime invokes them async-aware (sync → `to_thread`, async-native → awaited) — reuse the `_invoke` pattern from `tools/web_providers.py`.

## Integration points (LiveKit-native, already present in JARVIS)

- `JarvisAgent(Agent)` at `jarvis_agent.py:3537`; `on_user_turn_completed` already overridden at `:3548` (LiveKit's documented memory hook — "good opportunity to update chat context before it's sent to the LLM"). Add the sync + gated-recall there alongside the existing logic.
- `on_enter` / `on_exit` for session lifecycle (add if not present).
- `turn_router.is_recall_query` (`turn_router.py:437`) gates the auto-recall.
- Gated auto-recall injects via `turn_ctx.add_message(role="assistant", content="[memory] …")` — current-turn-only, appended at the tail so the cached instruction prefix is never invalidated.

## Error handling + latency discipline

- **Off critical path / swallowed:** `sync_turn_async` uses `asyncio.create_task` (fire-and-forget; exceptions logged, never raised). Gated auto-recall wrapped in try/except with a hard timeout (default 1.5s, env-tunable) — timeout/error → inject nothing, turn proceeds. Explicit `recall()` tool → clean `tool_error` on failure.
- **Provider down / no key / SDK missing:** `is_available()` false → recall tool filtered out, auto-recall skipped, sync skipped. file-memory unaffected. Zero turn impact.
- **Never blocks TTS:** dialectic `peer.chat()` only behind the explicit (LLM-invoked) tool; auto-recall uses cheap `get_context` + timeout.

## Activation / config

- `JARVIS_MEMORY_PROVIDER` — names the active backend (e.g. `honcho`). Unset → entire layer inert.
- Backend key (e.g. `HONCHO_API_KEY`) — required for `is_available()`.
- `JARVIS_MEMORY_RECALL_TIMEOUT_S` (default `1.5`) — auto-recall hard timeout.
- `honcho-ai` added to `requirements.txt`; absence → provider inert (import-guarded). mem0 similarly when added.

## Testing

- **Unit:** interface contract (recall NL-in/text-out; `sync_turn` never raises); `is_available` gating; `JARVIS_MEMORY_PROVIDER` selection + default-off; runtime no-ops when no provider. Fake provider, no network. (Use the conftest provider-registry isolation fixture already present.)
- **Integration (mocked Honcho client):** `begin_session`/`sync_turn`/`recall`/`recall_context` invoke the right SDK methods; gated auto-recall fires only when `is_recall_query` true; timeout path injects nothing; sync failure doesn't raise into the turn.
- **Regression:** full suite green; `import jarvis_agent` clean; file-memory tests untouched; no-dup guard; `on_user_turn_completed` existing behavior preserved.
- **Live (manual; needs `HONCHO_API_KEY` + `pip install honcho-ai` + `JARVIS_MEMORY_PROVIDER=honcho`):** two-session cross-session recall; confirm speech latency never stalls on sync or auto-recall.

## File plan

- **Create:** `pipeline/memory_provider.py` (runtime). The `recall` registry tool lives in `tools/memory_providers.py` (registers at import via `registry.register`, mirroring how `tools/web_providers.py` registers `web_extract`/`web_crawl`). Tests: `tests/test_memory_provider_runtime.py`.
- **Modify:** `tools/memory_providers.py` (real base methods); `plugins/memory/honcho/__init__.py` (real Honcho impl); `jarvis_agent.py` (`on_enter`/`on_user_turn_completed`/`on_exit` hooks — additive); `requirements.txt` (`honcho-ai`).
- **Untouched:** `pipeline/file_memory.py`, `tools/memory.py` (the `memory()` tool), the frozen-snapshot path (`_build_memory_block`).

## Out of scope / deferred

- mem0 + the other 6 memory backends staying inert mirrors (only Honcho gets the real impl this round; the interface is built to fit mem0 next).
- Replacing file-memory or moving curated facts into the provider (explicitly NOT done — augment only).
- Per-turn full auto-prefetch on *every* turn (rejected for latency; gated to recall-ish turns only).
- Migrating existing MEMORY.md/USER.md content into the provider.

## Risks

1. **Honcho dialectic latency** (multi-second) — mitigated by keeping it tool-only + cheap-path auto-recall + timeouts. If even the tool feels too slow in voice, fall back to `get_context` for the tool too.
2. **`on_user_turn_completed` already has logic** — must integrate additively without disturbing existing turn handling (read it first during implementation).
3. **Honcho SDK surface drift** — pin `honcho-ai`; use the high-level client; guard all SDK calls.
4. **Tool under-calling persists** even with gating if `is_recall_query` is too narrow — acceptable; tune the classifier separately if observed.

## Research sources

- LiveKit Agents 1.5.9 installed source: `on_user_turn_completed` (`agent.py:247`), `on_enter/on_exit`, `update_chat_ctx`. [Chat context](https://docs.livekit.io/agents/logic/chat-context/) · [External data & RAG](https://docs.livekit.io/agents/build/external-data/) · [mem0 + LiveKit](https://docs.mem0.ai/integrations/livekit)
- Honcho: [SDK reference](https://honcho.dev/docs/v2/documentation/reference/sdk) · [Dialectic endpoint](https://honcho.dev/docs/v2/documentation/core-concepts/features/dialectic-endpoint) · [honcho-ai (PyPI)](https://pypi.org/project/honcho-ai/)
- mem0: [async memory](https://docs.mem0.ai/open-source/features/async-memory) · [Python quickstart](https://docs.mem0.ai/open-source/python-quickstart)
