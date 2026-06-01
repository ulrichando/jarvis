# JARVIS Web-Nav Phase 2a — `computer_use` Vision-Feedback Loop

**Date:** 2026-05-30
**Status:** proposed (design) — awaiting review
**Scope:** make the supervisor *perceive* the screen after each `computer_use` action — actual
pixels when the active model is vision-capable, a fresh text description otherwise — so it plans
the next action from what's actually on screen instead of acting blind. **No `computer_use` schema
change. No provider hardcoding.**
**Builds on:** `2026-05-30-web-nav-enterprise-program-roadmap.md` (Phase 2, "close the
`computer_use` vision-feedback loop" — flagged as the highest-leverage capability change). P2b
(AT-SPI2 + Set-of-Marks) and P2c (bounded self-verification, which depends on this) are separate
later specs.

## Why this, why now

`computer_use` is a primitive action surface: it captures a screenshot but returns only a JSON
summary, because the LiveKit tool result (`FunctionCallOutput.output`) is typed **`str`** — pixels
can't ride back inside a tool result (verified: `livekit.agents.llm.FunctionCallOutput.output: str`).
So today the supervisor clicks, gets `{"ok": true, ...}`, and is **blind to the result** until it
calls another tool. That blindness is the single biggest cap on desktop-GUI reliability. The
existing partial mitigation (`screen_share_observer` threads a vision-model *description* into the
capture response — `tools/computer_use.py::_capture_response`) only runs while the user is
screen-sharing and is a description, not the screen.

## Goal

After a `computer_use` action, the supervisor's next generation includes the post-action screen:
the real screenshot as an image when the active supervisor model can see, a fresh text description
otherwise. Provider-agnostic, ephemeral (no history bloat), latency-conscious.

**Explicit non-goals (this increment):** no `computer_use` schema/shape change (keeps the
`anthropic_strict_schema` patch untouched); no bounded self-verification loop (P2c); no AT-SPI2 /
Set-of-Marks (P2b); no fixing the stale "no accessibility tree" comments (P2b owns those); no new
provider; no persisted screenshot history.

## Verified mechanism (the design rests on these, checked against the installed 1.5.14)

1. **`ImageContent` → native image block, centrally, for every provider.** `ChatContext.to_provider_format(fmt)`
   (`chat_context.py:648`) dispatches to `_provider_format.{anthropic,openai,google,aws}.to_chat_ctx`.
   A live conversion of a `ChatMessage` containing `ImageContent(image="data:image/png;base64,…")`
   produced `{"type":"image","source":{"type":"base64","media_type":"image/png","data":…}}`. So
   injecting an `ImageContent` is provider-agnostic — no per-plugin work. (`ImageContent.image` is a
   **data URL** string; `chat_context.py:204`.)
2. **`llm_node` owns the generation input and gets a per-generation COPY.** `Agent.llm_node(self,
   chat_ctx, tools, model_settings)` is the node that runs each LLM generation. The framework passes
   it a **copy** (`agent_activity.py:2512` `chat_ctx = chat_ctx.copy()` → `:2529 node=self._agent.llm_node`).
   ⇒ mutating that `chat_ctx` inside an `llm_node` override is **ephemeral** — it shapes exactly one
   generation and never persists to canonical history. **Eviction is therefore free.**
3. **The `str`-only result is structural, not just the adapter.** `tools/_adapter.py` str-coerces,
   and `FunctionCallOutput.output` is `str` — so the screenshot fundamentally cannot travel in the
   `computer_use` tool result; it must be a separate `ChatMessage`. This is why injection (not
   "return the image") is the design.
4. **The supervisor is Claude (vision-capable) by default, per route.** `providers/llm.py::_ANTH_DEFAULT_PER_ROUTE`
   (lines 930-939) maps every route to `claude-haiku-4-5`/`claude-sonnet-4-6` (env-overridable);
   `TASK_DESKTOP`→`claude-sonnet-4-6`. The live supervisor LLM is the per-route `DispatchingLLM`
   (`build_dispatching_llm`), reachable as the module-global `_dispatch_llm` in `jarvis_agent.py`
   (exposes `.last_route` / `.last_llm_label`). `SPEECH_MODELS`/`read_speech_model()` is a separate
   tray registry (defaults to a Groq model) and is **not** the supervisor source — hence the gate
   defaults to pixels-on (Decision 4), not a conservative text path.

## Design

### Decision 1 — provider-agnostic `ImageContent` injection (not a `Toolset` rebuild)
Inject the post-action screenshot as a `ChatMessage` (role `user`, a short bracketed label + an
`ImageContent`). Rejected alternative: rebuilding `computer_use` as an `llm.Toolset` returning image
content blocks (the plugin's `computer_tool.py` pattern) — it's Anthropic-coupled and a bigger
rewrite, and the central `to_provider_format` already makes plain injection provider-agnostic, so it
buys nothing.

### Decision 2 — inject in an `llm_node` override (ephemeral, correct timing)
`computer_use` stays a pure `RawFunctionTool` returning its str summary; it additionally **publishes**
the latest frame to a module-level cache. `JarvisAgent.llm_node` (override) reads the cache and, if a
capture is *current*, injects one message into the per-generation `chat_ctx` copy before delegating
to `super().llm_node(...)`. Timing is guaranteed (the node IS the generation); ordering and eviction
are automatic (copy is discarded after the generation). Not the tool handler (mutating live chat_ctx
mid-execution is timing-fragile) and not `on_user_turn_completed` (fires on the user turn, not after
a tool).

### Decision 3 — the capture cache (keeps `computer_use` testable)
A small module-level cache `pipeline/computer_use_vision.py::{publish_capture, take_current}`
(mirrors `screen_share_observer.latest_description_global()`): holds `{png_b64, width, height,
action_label, ts_monotonic}` for the **newest** frame only. `computer_use`'s `capture`/post-mutation
path calls `publish_capture(...)`. A new user turn (`on_user_turn_completed`) clears it so a stale
screen never bleeds into an unrelated turn. Pure functions → unit-testable without a session.

### Decision 4 — vision gate: `JARVIS_CU_VISION_MODE` (default pixels-on) + best-effort detection
**Discovery (verified, amended 2026-05-30):** every per-route SUPERVISOR default in
`providers/llm.py::_ANTH_DEFAULT_PER_ROUTE` (lines 930-939) is **Claude** — `claude-haiku-4-5` /
`claude-sonnet-4-6`, all vision-capable; `TASK_DESKTOP`→`claude-sonnet-4-6`. (`SPEECH_MODELS` /
`read_speech_model()` is a SEPARATE tray registry that defaults to a Groq model — it is **NOT** the
supervisor source; gating on it would be wrong. The live supervisor LLM is the per-route
`DispatchingLLM` built by `build_dispatching_llm`.) So the supervisor sees images by default; the
gate's only job is to avoid sending an image to a text-only model when an operator has overridden a
route (`JARVIS_{ROUTE}_MODEL`) to Groq/DeepSeek or the active rung fell back.

Gate = env `JARVIS_CU_VISION_MODE` (default `auto`):
- `off` → never inject.
- `text` → always inject the text-description fallback (operator running a text-only supervisor).
- `pixels` → always inject pixels.
- `auto` (default) → **best-effort** detect the active route's model: read the module-global
  `_dispatch_llm` (the live `DispatchingLLM`) `.last_route`, resolve its primary model via a new
  public `providers/llm.py::resolve_route_primary_model(route)`, and check a vision allowlist
  (prefixes `claude-`/`gpt-4o`/`gpt-4.1`/`gemini-`, env-overridable `JARVIS_VISION_MODEL_PREFIXES`).
  Inject **pixels** if vision-capable, **text-description** if confidently text-only, and **pixels on
  ANY uncertainty** (detection failure / unknown route / no `_dispatch_llm`) — because the canonical
  supervisor is Claude. Detection is best-effort, never load-bearing.

Text-description fallback source: `screen_share_observer.latest_description_global()` if present,
else inject nothing (the str summary already returned).

### Decision 5 — newest-frame-only, downscaled, with a cheap action trail
Only the newest frame is ever injected (no accumulation; the copy is ephemeral so older frames are
already gone). The image is downscaled to ~1280px longest edge before encode (and/or
`inference_detail="auto"`) to bound vision tokens — this is a voice hot path; TTFW matters. A short
text "recent computer_use actions" trail (last ~3 action labels, text only) is appended to the
injected message to give the supervisor cheap recent-history context (the roadmap's Gemini
refinement) without extra images. Freshness window `JARVIS_CU_VISION_TTL_S` (default 20s).

## Components

| Unit | Responsibility | Depends on |
|---|---|---|
| `pipeline/computer_use_vision.py` (new) | `publish_capture(...)` / `take_current(ttl)` / `clear()` newest-frame cache; `is_vision_capable(model_id)`; `downscale_png(b64,max_px)` — all pure | stdlib, `livekit.agents.utils.images` |
| `tools/computer_use.py` (modify) | after `capture` / post-mutation, call `publish_capture(...)` with the existing `CaptureResult` (`png_b64`,`width`,`height`) + an action label. No schema change. | the cache module |
| `jarvis_agent.py` (modify) | add `JarvisAgent.llm_node` async-generator override: read cache → decide mode (`JARVIS_CU_VISION_MODE`, `auto` best-effort via module-global `_dispatch_llm.last_route`) → inject `ImageContent` (pixels) or text description into the per-gen `chat_ctx` copy → `async for chunk in Agent.default.llm_node(self, ...): yield chunk`. Clear the cache in the existing `on_user_turn_completed`. | cache module, `providers/llm.py::resolve_route_primary_model`, `_dispatch_llm`, `ImageContent` |
| `providers/llm.py` (modify) | add public `resolve_route_primary_model(route) -> str` — extract the env-override→`_ANTH_DEFAULT_PER_ROUTE` default resolution (currently nested in `build_dispatching_llm._resolve_route_model`) into a module-level helper the gate can call | — |

## Data flow

```
supervisor turn → computer_use(action) → backend acts + captures frame
   → publish_capture(png_b64,w,h,label,ts)        # newest-frame cache
   → tool returns its str summary (unchanged)      # FunctionCallOutput.output: str
framework appends FunctionCallOutput, starts the follow-up generation:
   → JarvisAgent.llm_node(chat_ctx_COPY, tools, settings)   # async generator override
       cap = take_current(ttl=JARVIS_CU_VISION_TTL_S)
       mode = decide_mode()   # JARVIS_CU_VISION_MODE, default 'auto' → best-effort via _dispatch_llm.last_route
       if cap and mode == "pixels":
            chat_ctx_copy.add_message(role="user",
                content=[f"[screen after: {cap.label}]{recent_actions_text()}",
                         ImageContent(image="data:image/png;base64,"+downscale_png(cap.png_b64),
                                      inference_detail="auto")])
       elif cap and mode == "text":
            desc = screen_share_observer.latest_description_global()
            if desc: chat_ctx_copy.add_message(role="user", content=[f"[screen after: {cap.label}] {desc}"])
       async for chunk in Agent.default.llm_node(self, chat_ctx_copy, tools, settings):  # supervisor SEES the screen
            yield chunk
new user turn → on_user_turn_completed → computer_use_vision.clear()
```

## Error handling
Capture/encode/downscale/describe failure → inject nothing; the generation proceeds on the str
summary alone. Never raise out of `llm_node` for a vision-injection problem (best-effort, mirrors the
tool's telemetry posture). A missing/old cache entry (past TTL) → no injection.

## Testing
Pure unit tests (no session):
- `is_vision_capable`: `claude-sonnet-4-6`/`gpt-4o`/`gemini-2.0-flash` → True; `llama-3.3-70b-versatile`
  → False; unknown → False; env-prefix override respected.
- `publish_capture`/`take_current`: newest wins; past-TTL returns None; `clear()` empties; second
  `take_current` within TTL still returns (idempotent re-inject is fine — ephemeral).
- `downscale_png`: a >1280px PNG shrinks to ≤1280 longest edge; tiny PNG unchanged; bad bytes → None.
- An `llm_node`-injection unit test against a fake `chat_ctx`: vision model → an `ImageContent` is
  added; text-only model with a cached description → a text message is added; no cache → ctx unchanged.
- Regression: full `pytest` green; `computer_use` schema unchanged (the `anthropic_strict_schema`
  patch is untouched — no object-node shape edits).

**Live acceptance (manual, like P1):** with a vision-capable supervisor, a `computer_use` action
followed by a generation shows the supervisor referencing actual on-screen content it was not told
in text; flipping the active route to a Groq text-only model falls back to the description path with
no error and no image sent.

## Risks
- **Token/latency on the hot path** — mitigated by newest-only + downscale + TTL + ephemeral (never
  persists). One image per follow-up generation during an active desktop task; gone otherwise.
- **Synthetic `user` message for the image** — images must be in user/tool content (not system) for
  Anthropic; a clear bracketed label marks it as a system-provided screen so it doesn't read as the
  human talking. Ephemeral, so it never accretes in history.
- **Active-model resolution coupling** — the gate reads the route→model mapping in `providers/llm.py`;
  if that's unavailable the gate fails conservative (text path / no image), never crashes.
- **Mid-generation fallback to a text-only rung** — the gate decides on the route's *primary* model.
  If the primary is vision-capable (image injected) but the FallbackAdapter then drops to a text-only
  rung (Groq `llama-3.x`) because the primary errored, that rung receives an image block it can't use
  (OpenAI-format conversion still emits it). This is a rare, already-degraded path (the primary had to
  fail first); accepted for P2a. Hardening (strip `ImageContent` on the text-only rung — the framework
  already has the strip primitive at `chat_context.py:623`) is a noted follow-up, not built here.
- **`llm_node` override interaction** — JARVIS may already wrap generation; the override must call
  `super().llm_node(...)` and add only the injection, changing nothing else about generation.
