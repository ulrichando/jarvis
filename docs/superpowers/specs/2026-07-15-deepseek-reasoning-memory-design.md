# DeepSeek reasoning memory for the cloud voice (replaces honcho)

**Date:** 2026-07-15 · **Branch:** `feat/cloud-voice-memory` · **Status:** design, awaiting build

## Goal

Give the cloud voice agent (`src/voice-agent-lk`) semantic cross-session recall using **only DeepSeek + the Postgres stores already built** — no embeddings, no honcho, no extra provider/key. "A memory that reasons": DeepSeek retrieves candidate context from Postgres (full-text + recency) and synthesizes recall answers, and a background DeepSeek *distiller* turns raw conversation into clean, keyword-findable facts.

## Why (the decision, from the Fable exploration)

- **honcho hard-requires embeddings** (verified against v3.0.9: observation writes unconditionally embed; the only observation query is vector cosine; embedding transport is a hard `openai|gemini` literal). DeepSeek has no embeddings endpoint → **DeepSeek-only honcho is structurally impossible.**
- For a **single user**, honcho's value is the *reasoning*, not the vector retrieval (honcho itself ships a `grep` tool because vectors miss exact names/dates). Postgres full-text + DeepSeek reasoning covers the personal, named-entity-heavy corpus.
- **Gains:** one datastore + one backup story, fully inspectable memory (`SELECT * FROM memory_facts` vs opaque vectors), recall over **voice *and* web** history, 4 fewer VPS containers, no embed/deriver key, instant revert via a flag.
- **Honest loss:** one-shot paraphrase recall of a detail that was *never distilled* (rare for one user) — and it fails *safe* ("nothing matched" vs embeddings' confident-wrong-but-similar).
- The built honcho path (committed `059ea257`) is **kept behind `RECALL_BACKEND`** as an instant-revert fallback; its deploy config is archived, not deleted, until DeepSeek recall proves out live.

## Architecture

Keeps the existing `/api/recall` contract (`POST {mode:"query"|"sync", user_id, query}`) so `agent.py`'s recall tool is unchanged. Internal switch on **`RECALL_BACKEND`** (`deepseek` default | `honcho` fallback).

### Retrieval (no embeddings)
- `web.msg_text(jsonb)` SQL function: extract plain text from `messages.content` JSONB (the `[{type:'text',text}]` shape).
- GIN **tsvector** index on `web.messages` (over `msg_text`); `pg_trgm` extension for fuzzy/misspelling.
- Query: FTS over `web.messages` + `web.memory_facts`, ranked **relevance × recency decay**. If `<3` hits, one `deepseek-v4-flash` call generates 6–10 synonym/spelling variants → re-query. Pull **±2 neighbor turns** per hit for context.

### Recall synthesis (query-time, LLM-chosen tool)
- `deepseek-v4-flash` (env `RECALL_MODEL`) synthesizes a **≤120-word spoken answer** from retrieved context, instructed to say **"not present"** rather than invent. Fits the recall tool's existing 30s budget.

### Distiller (write-time, background — the "reasoning memory")
- **Lazy trigger:** fire-and-forget on session start (`GET /api/memory`) when `>50` unprocessed turns. No cron.
- `deepseek-v4-pro` (env `DISTILLER_MODEL`) rewrites conversation batches into **atomic timestamped facts** → new `web.memory_facts` table (**rebuildable** — NOT `curated_memories`, whose budget/eviction contract would fight derived data) + a **≤1500-char rolling profile** doc.
- Every derived fact passes the **curated-store injection scan + meta-paraphrase filter** (reuse `lib/memory/curated.ts` scan) before storing — derived content still lands adjacent to a prompt.
- `web.memory_distill_state` cursor tracks the last-processed message per user.
- **Frozen-snapshot rule untouched:** facts feed the recall *synthesizer*, never the session prompt.

### Schema (raw SQL in `ensureWebSchema`, idempotent — web DB is drizzle-push managed; never `db:migrate`)
- `web.msg_text(jsonb)` function · GIN tsvector index on `web.messages` · `pg_trgm` extension
- `web.memory_facts(id, user_id → users.id, fact text, source_message_id?, created_at, ...)` + GIN index
- `web.memory_distill_state(user_id, last_message_id/cursor, updated_at)`

### Voice agent (`agent.py`)
- Recall tool unchanged (same `/api/recall` query call).
- **Remove** the now-redundant `mode:"sync"` / `_sync_recall_turn` / `_sync_lock` double-write — the deepseek backend reads `web.messages` directly (the turn already lands there via `/api/voice-memory`).

### Models & provider
- `DISTILLER_MODEL=deepseek-v4-pro`, `RECALL_MODEL=deepseek-v4-flash` (verify live ids — DeepSeek API is multi-model, text-only, fine here). Called direct against `api.deepseek.com` with the existing VPS `DEEPSEEK_API_KEY`; a base-URL env allows pointing at the JARVIS gateway later.

## Auth / non-blocking
- `/api/recall` keeps `voice-service-auth` (session OR `sub=voice-agent` 403-pinned). Recall is an LLM-chosen tool (multi-second, fine). Distiller is fire-and-forget background — never blocks or fails a turn.

## Testing
FTS ranking + recency decay, synonym-expansion path, synthesis "not present" behavior, distiller fact extraction + injection-scan rejection, the `RECALL_BACKEND` switch (deepseek vs honcho). DeepSeek mocked.

## Effort
Medium (~1–1.5 days): DDL + retrieval + synthesis + distiller + tests. Builds on Phases 0–1 (everything is already stored — no new ingestion path).

## Out of scope / later
- Option C (mini agentic recall loop) — only if B's recall misses in practice.
- Deleting the honcho config — only after DeepSeek recall proves out live.
