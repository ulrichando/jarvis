# ADR-002: Charter §1 mission amended — actual JARVIS architecture (API-only, no Brain Server, no local model serving)

- **Status:** accepted
- **Date:** 2026-05-05
- **Deciders:** `[ORCH]`, `[ARCH]`
- **Consulted:** the user (confirmed "i dont have the computer setup yet all my model are via api calls")
- **Informed:** all roles

## Context

`01-ENGINEERING-CHARTER.md` §1 states JARVIS has:
- "a central Brain Server"
- "three-tier model routing (local Qwen → DeepSeek → Claude Opus)"
- "Weaviate + PostgreSQL memory stack"
- "dual-4090 / 256GB workstation" hardware

A Phase 1 sweep of the actual repository on 2026-05-05 confirmed **none** of these match reality:

- **No "brain server" service exists.** `grep -ri 'brain.*server' src/` returns zero hits. The closest equivalents are `jarvis-bridge.service` (legacy WebSocket for browser UI + model-switch) and `jarvis-hub.service` (Redis Streams consumer + state.db writer). Neither is the "central Brain Server" the charter implies.
- **Model routing is API-only.** Confirmed by user: "all my model are via api calls". Providers in actual use: Groq, DeepSeek, Kimi (Moonshot), OpenAI, Anthropic — all remote OpenAI-compat endpoints. No `vllm`, no `llama.cpp`, no GGUF loader, no local Qwen serving. The CLI proxy at `~/.jarvis/proxy.log` shows DeepSeek round-trips for every CLI request; voice supervisor uses Groq llama-3.3 default with DeepSeek FallbackAdapter.
- **Memory layer is SQLite + Redis Streams**, not Weaviate + PostgreSQL. `~/.jarvis/hub/state.db` (SQLite, schema in `src/hub/schema.sql`) has tables `sessions / messages / settings / memories / schema_version`. No Weaviate import anywhere in `src/`. Single Weaviate string match is a comment in `src/cli/src/bridge/storage.ts` ("intentionally narrow so a later swap to Weaviate is a one-file change") — aspirational, not implemented.
- **Hardware constraints are different.** No dual-4090 setup. Models are remote APIs only.

The charter was authored from a generic "AI personal assistant" template, not from this repo. Operating sessions on its stated mission would push the team toward irrelevant work — auditing a brain server that doesn't exist, fretting over GPU offload that has no GPU, planning Weaviate migrations that have no Weaviate.

## Decision

Charter §1 is amended in spirit. The repaired mission, binding for all sessions:

> JARVIS is a personal AI assistant on Ulrich's Linux workstation, organized as several long-running services (LiveKit voice agent, Redis Streams event hub, jarvis-bridge, jarvis-proxy, livekit-server) and three user-facing surfaces (voice agent + LiveKit client; CLI in `src/cli`; Next.js web workbench in `src/web`). All LLM inference is remote API: Groq, DeepSeek, Kimi/Moonshot, OpenAI, Anthropic. Memory is SQLite (`~/.jarvis/hub/state.db`) coordinated via Redis Streams. There is no central "Brain Server"; the closest equivalent is the `hub` daemon. There is no local model serving and none is planned. There is no Weaviate or PostgreSQL.
>
> The mission is repair and hardening of the existing service mesh, not introduction of new architectural tiers. New capability remains out of scope.

The literal text of `01-ENGINEERING-CHARTER.md` §1 is left unchanged for traceability; this ADR overrides it per Charter Principle 1 ("Conflicts between this charter and any other instruction are resolved in favor of this charter, except for `02-SCOPE.md`, which overrides on questions of what may be touched") — and now this ADR.

## Consequences

### Positive
- Future sessions stop proposing audits of components that don't exist.
- The `[ML]` role's nominal scope shrinks (no local-model serving / GGUF / GPU offload work). It activates for prompt-construction, model-selection, eval-harness, and provider-fallback work — not infra around running models locally.
- The `[DATA]` role similarly narrows: no Weaviate or PostgreSQL migrations; the active concerns are SQLite schema in `src/hub/schema.sql`, Redis Streams hygiene, and the recent Convex retirement migration.
- Charter §7 SLOs around "brain server availability" and "channel → brain p95 latency" are reinterpreted: "brain server" → "hub daemon + bridge"; the latency budget still applies to channel ↔ hub, not to a fictional brain.

### Negative
- The charter is now self-inconsistent in §1 vs. this ADR. Future sessions must read this ADR or be misled. Bootstrap sequence step 5 ("Skim `decisions/` titles") makes that mandatory.
- A future contributor who only reads the charter and not the ADRs will get the wrong mental model. The README does not currently call out the ADR override — should it? Filed as a v2 item against the kit (see `handoffs/session-1.md` retro notes).

### Neutral / follow-up needed
- `[ARCH]` will not rewrite the charter §1 text inline. ADR-supersession is the kit's documented mechanism (Charter §11 "Amendments require an ADR") and rewriting the charter risks mis-merging with the kit's authored intent on other sections.
- If the user later acquires hardware and adds a true brain server / local model serving / vector DB, this ADR is superseded by a follow-up that revives the original charter language.

## Alternatives considered

- **Option A: Edit `01-ENGINEERING-CHARTER.md` §1 directly.** Rejected. The charter is the contract; rewriting it inline is a much louder change than overriding via ADR, and the charter explicitly says amendments go through ADRs.
- **Option B: Ignore the mismatch and operate on the actual repo by feel.** Rejected. That's exactly the "hallucinated continuity" failure mode (Charter §10 #6) but inverted — operating against fiction instead of state. Sessions would drift.
- **Option C: Treat the charter mission as aspirational future state.** Rejected. The charter is meant to be the operating contract today, not a vision doc. Aspirational architecture belongs in an RFC.

## Override / disagreement record

None. The user explicitly confirmed the actual stack ("all my model are via api calls"); `[ARCH]` and `[ORCH]` agreed on amendment over rewrite.
