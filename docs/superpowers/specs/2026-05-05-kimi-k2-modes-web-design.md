# Kimi K2.6 modes — proper web chat integration

**Date:** 2026-05-05
**Status:** Approved scope (Tier 2 across all 4 modes; per-mode handlers; Vercel AI SDK native primitives)
**Surface:** Web chat only (`src/web/`). CLI + voice already register Kimi but stay model-only.

---

## 1. Problem (in two sentences)

Today the four K2.6 entries (`kimi-k2-instant`, `-thinking`, `-agent`, `-swarm`) all map to the same upstream API model `kimi-k2.6` with only `temperature` differing — the modes are label-only, indistinguishable in behavior. To match what kimi.com actually delivers (and what the labels promise to the user) each mode needs real differentiation: `thinking` parameter for Instant/Thinking, `$web_search` + tool loop for Agent, and a decompose-fan-out-aggregate orchestration for Swarm.

## 2. Goals

- **G1 — Instant feels fast and minimal.** `thinking:disabled`, low max_tokens, single completion. No reasoning leakage.
- **G2 — Thinking surfaces reasoning honestly.** `thinking:enabled, keep:all`, `temp:1.0`, `max_completion_tokens≥16000`, stream the `reasoning_content` field as a collapsible "Thinking..." UI block that's separate from the answer body.
- **G3 — Agent uses Moonshot's native primitives.** `$web_search` builtin tool + your existing `webSearchTool` + multi-turn tool-loop with cost guards. Honest about the `$web_search`/`thinking` incompatibility.
- **G4 — Swarm actually swarms.** Real decompose → 3-5 parallel `chat/completions` calls (with `prompt_cache_key` for shared prefix) → aggregator that streams to UI. Per-day budget guard.
- **G5 — Each mode degrades gracefully.** Every failure path either retries with degraded params or falls back to a working mode; never silent failure or hard error in the UI.
- **G6 — Each mode is independently testable.** Mode handlers live in their own files with focused unit tests + one E2E test each.

## 3. Non-goals

- **NG1 — CLI changes.** Kimi is registered in `src/cli/src/utils/model/jarvisModelRegistry.ts` (commit `3b0178c`); CLI keeps its current single-call shape per mode. CLI users who want Swarm-style behavior can use the web chat UI.
- **NG2 — Voice agent changes.** Kimi is registered in `voice-agent/jarvis_agent.py` `SPEECH_MODELS`; voice keeps its current single-call shape. Multi-call orchestration in voice would blow the latency budget.
- **NG3 — Vision modes.** `kimi-vision-{8k,32k,128k}` continue using the existing chat path. K2.6 mode handlers are text-only.
- **NG4 — Cross-session swarm state.** Swarm decomposition + sub-agent results are per-turn ephemeral; no persistence between turns.
- **NG5 — Tool registry expansion.** Agent mode uses the existing `webSearchTool` + Moonshot's `$web_search`. Adding new custom tools (file upload, code interpreter, image gen) is out of scope.
- **NG6 — Replacing the existing chat route.** All non-K2.6 models keep their current code path. K2.6 handlers are additive — invoked via a single routing line at the top of the existing route.

## 4. Architecture

### 4.1 Topology

```
                   POST /api/chat
                         │
                         ▼
              chat route (route.ts)
                         │
              ┌──────────┴──────────────────┐
              │ if modelId starts kimi-k2-  │
              ▼                             ▼
       routeKimiMode(req, modelId)    existing chat logic
       (NEW: src/lib/ai/kimi/)        (unchanged path for
              │                        non-K2.6 models)
       ┌──────┼──────┬──────┐
       ▼      ▼      ▼      ▼
   instant  thinking agent swarm
       │      │      │      │
       │      │      │      └─► generateObject (decompose)
       │      │      │          → Promise.all(generateText × N)
       │      │      │          → streamText (aggregate)
       │      │      │
       │      │      └─► streamText with tools=[webSearch] + maxSteps=5
       │      │
       │      └─► streamText with thinking:enabled,keep:all + temp:1.0 + max=16k
       │          → split reasoning_delta vs text_delta → UI parts
       │
       └─► streamText with thinking:disabled + max=1024
                                       │
                                       ▼
                            Moonshot K2.6 API
                                       │
                                       ▼
                              UI streams (SSE)
```

### 4.2 Routing invariant

> **Every request with `model.startsWith('kimi-k2-')` enters the per-mode handler dispatcher BEFORE the existing chat-route logic. All other models (including `kimi-vision-*`) keep their current path unchanged.**

This is the only required edit to the chat route — one early-return after model resolution. Keeps blast radius minimal; non-K2.6 behavior is bit-for-bit unchanged.

## 5. Components

### 5.1 New package — `src/web/src/lib/ai/kimi/`

| File | Purpose | LOC est. |
|------|---------|----------|
| `index.ts` | Public API: `routeKimiMode(req, modelId, ctx) → Response`. Switch on the `kimi-k2-{instant,thinking,agent,swarm}` suffix and dispatch. | ~30 |
| `shared.ts` | Common helpers: `buildKimiClient()`, `extractMessages(req)`, `formatKimiError(err)`, `loadKimiPersona(req)` | ~80 |
| `instant.ts` | `handleInstant(req, ctx)` — `streamText({ providerOptions: { kimi: { thinking: { type: 'disabled' } } }, max_tokens: 1024 })` | ~30 |
| `thinking.ts` | `handleThinking(req, ctx)` — `streamText({ providerOptions: { kimi: { thinking: { type: 'enabled', keep: 'all' } } }, max_tokens: 16000, temperature: 1.0 })` + stream transformer that splits `reasoning_delta` vs `text_delta` | ~50 |
| `agent.ts` | `handleAgent(req, ctx)` — `streamText` with tools (existing `webSearchTool` + optional Moonshot `$web_search` builtin), `providerOptions: { kimi: { thinking: { type: 'disabled' } } }`, `stopWhen: stepCountIs(5)` | ~80 |
| `swarm.ts` | `handleSwarm(req, ctx)` — `generateObject` (decompose) → `Promise.allSettled(generateText × N)` (fan-out with `prompt_cache_key`) → `streamText` (aggregate). Per-day budget guard via Redis counter. | ~80 |

### 5.2 New UI components — `src/web/src/components/chat/`

| File | Purpose |
|------|---------|
| `reasoning-block.tsx` | Collapsible "Thinking..." block displayed above the assistant's text body. Shows reasoning_content as it streams; default-collapsed after streaming completes. |
| `tool-trace.tsx` | Inline breadcrumbs: "🔍 Searched: 'X'" → "📄 Got 5 results" — one entry per tool call/result pair from Agent mode |
| `swarm-progress.tsx` | "🌐 Coordinating 5 agents... 3/5 completed" indicator shown during Swarm fan-out, replaced by the streamed aggregator output once that begins |

(If existing components in `chat/` already cover the visual primitives — particularly for tool calls — reuse rather than duplicate. Verified at implementation time.)

### 5.3 Modified existing files

| File | Change |
|------|--------|
| `src/web/src/app/api/chat/route.ts` | Add one early-return after model resolution: `if (modelId.startsWith('kimi-k2-')) return routeKimiMode(req, modelId, ctx)`. ~5 lines added; existing logic untouched. |
| `src/web/src/components/chat/message.tsx` | Render `<ReasoningBlock>`, `<ToolTrace>`, `<SwarmProgress>` based on UI part types streamed from the new handlers. Existing rendering stays for non-K2.6 messages. ~30 lines added. |

### 5.4 Shared types — extend Vercel AI SDK UI parts

Three new UI part discriminators emitted by the K2.6 handlers (consumed by the chat client):

```typescript
type KimiReasoningPart  = { type: 'kimi-reasoning';      delta: string }
type KimiToolTracePart  = { type: 'kimi-tool-trace';     toolName: string; phase: 'call' | 'result'; data: unknown }
type KimiSwarmStatusPart = { type: 'kimi-swarm-status';   total: number; completed: number; current?: string }
```

Pass through `streamText`'s `experimental_dataPart` mechanism (Vercel AI SDK 6 supports custom UI parts).

## 6. Data flow per mode

### 6.1 Instant
1. UI POST → chat route → `routeKimiMode` → `handleInstant`
2. `streamText({ model: kimi, providerOptions: { kimi: { thinking: { type: 'disabled' } } }, max_tokens: 1024, temperature: 0.6 })`
3. SSE stream → `result.toUIMessageStreamResponse()` → standard text deltas to UI
4. `<Message>` renders deltas live; `reasoningTokens` badge shows 0

### 6.2 Thinking
1. UI POST → chat route → `routeKimiMode` → `handleThinking`
2. `streamText({ model: kimi, providerOptions: { kimi: { thinking: { type: 'enabled', keep: 'all' } } }, max_tokens: 16000, temperature: 1.0 })`
3. Moonshot SSE: `reasoning_delta` chunks arrive **before** `text_delta` chunks
4. Stream transformer routes parts:
   - `reasoning_delta` → `experimental_dataPart('kimi-reasoning')` → `<ReasoningBlock>` (collapsible, expanded-while-streaming)
   - `text_delta` → standard text part → main message body
5. UI renders both side-by-side live; on stream end, ReasoningBlock auto-collapses with a "Show thinking" toggle

### 6.3 Agent
1. UI POST → chat route → `routeKimiMode` → `handleAgent`
2. `streamText({ model: kimi, tools: { webSearch: webSearchTool, ...moonshotWebSearch }, providerOptions: { kimi: { thinking: { type: 'disabled' } } }, stopWhen: stepCountIs(5) })`
3. Moonshot SSE: text → tool_call → server runs tool → tool_result → text → … (loop)
4. Stream events:
   - `text_delta` → main message body
   - `tool_call` → `experimental_dataPart('kimi-tool-trace', phase: 'call')` → `<ToolTrace>`
   - `tool_result` → `experimental_dataPart('kimi-tool-trace', phase: 'result')` → updates the same `<ToolTrace>` entry
5. UI shows inline breadcrumbs as the loop progresses; final answer at the bottom

### 6.4 Swarm
1. UI POST → chat route → `routeKimiMode` → `handleSwarm`
2. **Step 1 — Decompose** (synchronous wait, ~1s):
   - `generateObject({ model: kimi, schema: SwarmPlanSchema, prompt: 'decompose into 3-5 parallel subtasks, each with role + prompt' })`
   - Returns `{ subtasks: [{role, prompt}, …] }`
3. Emit `experimental_dataPart('kimi-swarm-status', { total: N, completed: 0 })` → UI shows `<SwarmProgress>`
4. **Step 2 — Fan out** (parallel, ~3-8s):
   - `Promise.allSettled(plan.subtasks.map(t => generateText({ model: kimi, system: `You are agent ${t.role}`, prompt: t.prompt, providerOptions: { kimi: { prompt_cache_key: 'swarm-<sessionId>' } } })))`
   - As each completes, emit `experimental_dataPart('kimi-swarm-status', { total: N, completed: ++count })`
5. **Step 3 — Aggregate** (streamed to UI, ~2-4s):
   - Build aggregator prompt: `subResults.map(r => '## ' + r.role + '\n' + r.text).join('\n\n')`
   - `streamText({ model: kimi, system: 'Synthesize these results into one coherent reply', prompt: ... })`
   - Standard `text_delta` parts → UI message body (replaces the SwarmProgress widget)

**Total Swarm latency budget:** ~6-13s end-to-end (1 + max(parallel) + 4)
**Total Swarm cost per turn:** ~$0.06 (5 sub-agents at K2.6 prices, with `prompt_cache_key` reducing input cost)

## 7. Error handling

| Failure | Behavior |
|---------|----------|
| Moonshot API down (5xx, network) | Caught in handler → `formatKimiError()` returns SSE with error part → UI: friendly toast + retry button |
| 401 (invalid `KIMI_API_KEY`) | Caught at `buildKimiClient()` → 401 Response → UI: "Kimi key missing/invalid — check settings" |
| 429 (rate limit) | SSE with retry-after hint → UI: "Rate limited — retry in N seconds" |
| Thinking: no `reasoning_content` arrives after 3s | Log warning; treat all output as text. UI shows just the text body, no ReasoningBlock, no crash |
| Thinking: `max_completion_tokens=16k` rejected | One-shot retry with `max_tokens: 8000` (lower-tier ceiling); log degradation. UI gets reply, slightly truncated reasoning |
| Agent: `$web_search` returns no results | streamText loop continues; LLM either tries another tool or answers honestly. UI: tool-trace shows empty result; final answer reflects gap |
| Agent: tool loop hits `stopWhen: stepCountIs(5)` | streamText returns whatever's accumulated. UI: shows partial answer + small "tool-loop limit reached" hint |
| Swarm: decompose returns empty `subtasks` | Fall back to single Instant call (skip Swarm). UI: warning toast "Swarm decomposition failed, using Instant" + answer arrives normally |
| Swarm: ≥1 sub-agent throws | `Promise.allSettled`; failed sub-agents replaced with `"(this sub-agent failed: <reason>)"` in aggregator input. UI: SwarmProgress shows partial completion; aggregator handles missing-result gracefully |
| Swarm: aggregator throws | Catch + return concatenation of sub-agent results with section headers. UI: gets raw sub-agent outputs as fallback |
| `providerOptions.kimi.thinking` rejected (passthrough doesn't work) | Discovered at impl time; fall back to `fetch()` direct API call. Document in spec; no runtime fallback (this is a config issue, not a transient one) |
| Cost guard: per-day Swarm budget exceeded ($5/day default, configurable via `KIMI_SWARM_DAILY_BUDGET_USD`) | Swarm handler refuses; returns SSE explaining + suggests Instant/Thinking. UI: "Swarm budget reached for today, switch mode or wait" |

**Principle:** every mode degrades gracefully. Worst case is "Swarm degrades to Instant" or "Thinking shows just text without ReasoningBlock" — never a silent failure or a hard error in the chat UI.

## 8. Testing strategy

### 8.1 Unit (Vitest, ~35 tests)

| File | Tests |
|------|-------|
| `kimi/instant.test.ts` (4) | provider params correct; `thinking:disabled`; `max_tokens:1024`; standard SSE format |
| `kimi/thinking.test.ts` (8) | `thinking:enabled,keep:all`; stream split into reasoning + text; fallback-no-reasoning; retry on 16k rejection; reasoningTokens badge populated; ReasoningBlock receives data parts; collapse-on-end; persona override |
| `kimi/agent.test.ts` (10) | tools bound; `thinking:disabled`; `maxSteps:5`; tool_call events surface; empty search handled; loop ceiling handled; `$web_search` fallback when not available; multi-tool branching; web_search/thinking incompatibility verified; toolTrace data parts emitted |
| `kimi/swarm.test.ts` (10) | decompose ≥3 subtasks; `Promise.allSettled` semantics; fallback to Instant on empty plan; failed sub-agents → placeholder; `prompt_cache_key` set; per-day budget enforced; SwarmProgress data parts (start/progress); aggregator retries on transient failure; aggregator-throws fallback to raw sub-results; sessionId-scoped cache key |
| `kimi/shared.test.ts` (3) | `formatKimiError` SSE shape; `extractMessages` normalization; `loadKimiPersona` reads correctly |

Mock Vercel AI SDK calls (`generateText`, `streamText`, `generateObject`) — no real Moonshot API calls in unit tests.

### 8.2 Integration (Vitest + MSW, ~6 tests)

| Test | Coverage |
|------|----------|
| Instant E2E | POST → SSE → reply round-trip with mocked Moonshot responder |
| Thinking E2E | Mock Moonshot returns `reasoning_content` chunks before `content` chunks; verify UI parts emit in correct order |
| Agent E2E | Mock returns tool_call → tool_result → final text; verify ToolTrace events surface |
| Swarm E2E | Mock 3 sequential calls (decompose, fan-out, aggregate); verify SwarmProgress emits + final stream lands |
| Mode switch | Switch from Instant to Thinking mid-conversation; verify both work + chat history preserved |
| Error fallback | Mock 500 from Moonshot; verify formatKimiError SSE arrives + UI shows toast |

### 8.3 Live (manual after merge, ~10 turns each mode)

After the feature lands, drive:
- 5 Instant turns: simple chitchat + 1 tool-ish question (verify no tools fire)
- 5 Thinking turns: math/reasoning; verify reasoning block populates and is collapsible; verify final answer is grounded in the reasoning
- 5 Agent turns: questions requiring web search ("what's the weather", "latest news on X"); verify tool calls fire + tool-trace UI shows
- 3 Swarm turns: complex queries ("compare these 5 frameworks", "research these 3 topics"); verify decompose looks reasonable + aggregator merges coherently

Telemetry: log each turn to `turn_telemetry.db` with fields `mode, latency_ms, sub_agent_count, tokens_in, tokens_out, reasoning_tokens, tool_calls_count, error`.

### 8.4 Coverage targets

- **90%+ on per-mode handlers** (logic-heavy, must be tight)
- **80%+ on shared helpers**
- **E2E tests for each mode's happy path** (mandatory before merge)

## 9. Migration & rollback

**Phase 1 — code lands behind a build-time gate.** Default off (`process.env.KIMI_K2_MODES_ENABLED !== '1'`). When off, K2.6 entries continue using the existing single-call chat path (today's behavior). When on, the routing line in chat route activates.

**Phase 2 — soak.** Flag on for the dev rig. 24-48h soak with telemetry. Compare:
- Mode-correct rate (Thinking shows reasoning, Agent uses tools, Swarm fans out)
- Median latency per mode
- Cost per turn (especially Swarm)
- Error rate per mode

**Phase 3 — flip default.** If telemetry passes, set `KIMI_K2_MODES_ENABLED=1` as default in `.env.local` template / Next.js config. Keep flag for emergency rollback.

**Phase 4 — strip dead branches.** After 30 days of clean operation, remove the gating flag and the legacy K2.6 single-call path. Until then, both paths coexist.

**Rollback:** unset `KIMI_K2_MODES_ENABLED`, restart dev server / redeploy. ~30 seconds. K2.6 entries fall back to today's behavior.

## 10. Success criteria

- ✅ `kimi-k2-instant` produces no `reasoning_content` (verified via raw API capture in soak)
- ✅ `kimi-k2-thinking` emits `reasoning_content` and the UI renders a collapsible `<ReasoningBlock>` separate from the answer body
- ✅ `kimi-k2-agent` performs at least one tool call on a question that requires web information (e.g. "what's the weather in Paris right now")
- ✅ `kimi-k2-swarm` decomposes a complex query into ≥3 subtasks AND fires them in parallel (verified via stream timing)
- ✅ Each mode degrades gracefully under simulated failures (per §7 matrix)
- ✅ Median Instant latency < 1.5s on a baseline question
- ✅ Median Thinking latency < 6s; reasoning_content non-empty
- ✅ Median Agent latency < 5s on a no-tool question; < 10s with one tool call
- ✅ Median Swarm latency < 13s end-to-end
- ✅ Per-turn Swarm cost < $0.10 in the test scenarios
- ✅ ≥35 unit tests + 6 integration tests passing
- ✅ Zero regressions in non-K2.6 chat paths

## 11. Risks & mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| `providerOptions.kimi.thinking` doesn't pass through Vercel AI SDK's openai-compatible provider | Med | High | Discover at impl time via integration test; fall back to direct `fetch()` to Moonshot's `/v1/chat/completions`. Document the fallback path. |
| Moonshot's `$web_search` builtin not exposable through Vercel AI SDK tool format | Med | Med | Skip `$web_search`; use only the existing `webSearchTool`. UI label: "Agent" still meaningful. |
| Thinking reasoning_content tokens cost more than expected | Low | Med | Telemetry per turn; if median Thinking turn > $0.10, lower default `max_completion_tokens` to 8k. |
| Swarm decompose produces nonsensical subtasks | Med | Med | Schema-constrained `generateObject` with examples; budget guard catches runaway cost; fallback to Instant. |
| Swarm aggregator hallucinates beyond what sub-agents found | Low | Med | Aggregator system prompt explicitly says "synthesize ONLY from these sources"; flag for soak telemetry. |
| Per-mode handlers diverge from shared style (auth, error format, telemetry) | Med | Low | `shared.ts` enforces a base shape; code review checklist requires every mode use `formatKimiError()` and emit telemetry. |
| LangGraph-style multi-step orchestration creep — Swarm "should do" multi-round refinement | Low | Med | Explicitly out-of-scope (NG4); Swarm is one-pass: decompose, fan-out, aggregate. No iterative refinement. |
| Vercel AI SDK 6 `experimental_dataPart` API changes | Low | Med | Pin minor version; one place wraps the API (in shared.ts) so future changes are localized. |
| Reasoning UI block reads visually noisy on long chains | Low | Low | Default-collapsed after stream-end; user opt-in to view. |

## 12. File layout

**New (~700 LOC including tests):**

```
src/web/src/lib/ai/kimi/
├── index.ts                     # routeKimiMode dispatcher
├── shared.ts                    # client builder, error format, message extract, persona load
├── instant.ts                   # Instant handler
├── thinking.ts                  # Thinking handler + stream transformer
├── agent.ts                     # Agent handler + tool-loop wiring
└── swarm.ts                     # Swarm handler (decompose / fan-out / aggregate)

src/web/src/components/chat/
├── reasoning-block.tsx          # collapsible Thinking display
├── tool-trace.tsx               # Agent tool-call breadcrumbs
└── swarm-progress.tsx           # Swarm sub-agent counter

src/web/src/app/api/chat/route.ts        # MODIFIED: add 5-line K2.6 routing block
src/web/src/components/chat/message.tsx  # MODIFIED: render new UI part types

src/web/tests/kimi/
├── instant.test.ts
├── thinking.test.ts
├── agent.test.ts
├── swarm.test.ts
├── shared.test.ts
└── e2e.test.ts                  # 6 integration scenarios
```

---

## Self-review (per brainstorming skill — fix inline)

- [x] **Placeholder scan** — no TBD/TODO/incomplete sections in spec body. Phase 4 ("strip dead branches") is intentional deferral, not a gap.
- [x] **Internal consistency** — `KIMI_K2_MODES_ENABLED` flag named consistently across §9; `prompt_cache_key` cited consistently in §6.4 and §8.1; mode names (`kimi-k2-instant`, `-thinking`, `-agent`, `-swarm`) consistent throughout; `experimental_dataPart` cited consistently in §5.4 and §6.
- [x] **Scope check** — single subsystem (web chat for K2.6 modes); CLI + voice + vision + non-K2.6 web models all stay untouched (NG1, NG2, NG3, NG6). One implementation plan can cover this.
- [x] **Ambiguity check** — every mode's API params are explicit (`thinking:{type, keep}`, `max_tokens` values, `temperature`); error matrix in §7 enumerates every failure path; cost guards have explicit env vars (`KIMI_SWARM_DAILY_BUDGET_USD`) and defaults; the `$web_search`/`thinking` incompatibility is explicit (G3, §6.3, §11 risk).
