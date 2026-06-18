# CLI proxy folder — cleanup & upgrade

**Date:** 2026-06-15
**Scope:** `src/cli/src/proxy/` (+ two adjacent cruft items in `src/cli/`)
**Status:** design approved, pending spec review

## Context

`src/cli/src/proxy/` is the JARVIS multi-provider LLM proxy that the `jarvis`
CLI talks to on `127.0.0.1:4000`. It accepts Anthropic `/v1/messages` requests
and routes them to Anthropic-native passthrough or an OpenAI-compatible
upstream (DeepSeek / Groq / Kimi / OpenAI / Gemini / Ollama), with a
cross-provider fallback chain, retry/backoff, streaming + non-streaming
conversion, a DeepSeek reasoning-content round-trip cache, a first-party
DuckDuckGo web-search short-circuit, structured JSONL request logging, and an
optional login-minted HS256 inbound-auth gate.

This is **hand-written source with its own test suite** (142 passing tests
across 6 files at baseline) — not the compiled Claude-Code artifacts the rest
of `src/cli/` consists of. `src/cli/` is normally "ask before modifying"
(CLAUDE.md); the user has explicitly directed this work at the proxy folder.

The folder is healthy: clean module split, heavy comments, full green suite.
There are **no dead files** (`reasoning-cache.ts` looks unused but is imported
by `convert.ts` + `stream.ts`). So "remove unnecessary" means **internal
duplication and stray cruft**, not deleting modules.

## Goals

1. Remove copy-paste duplication (DRY) without changing wire behavior.
2. Fix one real robustness bug (mid-stream client disconnect).
3. Targeted upgrades: log rotation, true LRU reasoning-cache, typed stream
   blocks.
4. Normalize inconsistent JSON error envelopes to the Anthropic shape so the
   CLI surfaces real error messages.
5. Remove adjacent cruft (stale local logs, dead `package.json` test script).
6. Keep the suite green and grow it to cover every change.

## Non-goals (YAGNI)

- No module restructuring / no fuller `any` → typed-interface migration
  (that was the rejected "deeper refactor" option).
- No change to provider routing, fallback policy, retry policy, conversion
  semantics, or the inbound-auth design. (A provider cooldown/circuit-breaker is
  captured as a deferred option in §Optional — not in default scope.)
- No change to the streaming wire *bytes* or success-path response shapes; the
  Bun direct-mode switch (§J) changes only flush *timing*, not content.

## Verified findings (each confirmed against the code during design)

| # | Finding | Location |
|---|---------|----------|
| 1 | `PRIORITY` tool-set + truncate-by-priority logic byte-identical in two functions | `convert.ts:389` (`convertTools`), `convert.ts:573` (`clampRequestForProvider`) |
| 2 | `sseEvent()` defined identically twice | `stream.ts:34`, `webSearch.ts:126` |
| 3 | `safeSend` guards only the heartbeat; the `finally` closing-events use bare `send` → throws on disconnected client | `stream.ts` finally block (~L272–305) |
| 4 | Streaming `event: error` builder duplicated 2× | `server.ts:403`, `anthropicPassthrough.ts:127` |
| 5 | `text/event-stream` + `no-cache` header object repeated 5× | `server.ts` ×3, `anthropicPassthrough.ts` ×2 |
| 6 | `proxy.log` grows unbounded (only `appendFile`, no rotation) | `logger.ts:48` |
| 7 | `getReasoning` never refreshes recency → eviction is insertion-order, not LRU | `reasoning-cache.ts:73` |
| 8 | Untyped `(tb as any)._contentIndex` stash on the tool-block map | `stream.ts:213,227,280` |
| 9 | Three JSON error envelopes; 3 sites unwrapped (`{error:{…}}`) vs wrapped (`{type:'error',error:{…}}`) — SDK keys on top-level `type:'error'` | unwrapped: `server.ts:275,338,373`; wrapped: `server.ts:263,418,493` |
| 10 | `package.json` `"test"` is a dead `echo "Error: no test specified" && exit 1` | `src/cli/package.json:15` |
| 11 | `scripts/jarvis-proxy.log` + `.err.log` are **untracked** local cruft; `.err.log` references the dead `src/jarvis-cli` path; `.gitignore` has no `*.log` rule | `src/cli/scripts/`, `src/cli/.gitignore` |
| 12 | All SSE responses use the default `ReadableStream`+`enqueue()`, which Bun **batches** rather than flushing per chunk ([Bun #13923](https://github.com/oven-sh/bun/discussions/13923)) → added token-stream latency | `stream.ts`, `webSearch.ts`, `server.ts`, `anthropicPassthrough.ts` |

## Design

### A. New module — `src/cli/src/proxy/sse.ts`

Single home for the SSE plumbing duplicated across four files. Exports:

- `sseEvent(event: string, data: unknown): string` — the
  `event: …\ndata: …\n\n` builder (moved out of `stream.ts` + `webSearch.ts`).
- `SSE_HEADERS: Record<string,string>` — the `text/event-stream` / `no-cache`
  / `keep-alive` header object; call sites spread it and add their
  `x-jarvis-*` headers.
- `errorEventStreamResponse(message: string): Response` — the one-shot
  `event: error` SSE stream `Response` (replaces the identical bodies at
  `server.ts:403` and `anthropicPassthrough.ts:127`), emitting
  `{type:'error', error:{type:'api_error', message}}`.
- `jsonError(status: number, type: string, message: string, extraHeaders?:
  Record<string,string>): Response` — Anthropic-shaped JSON error
  `{type:'error', error:{type, message}}`.

### B. `convert.ts` — dedupe tool-priority (finding 1)

Hoist a module const `PRIORITY_TOOLS` and add
`truncateToolsByPriority<T>(tools: T[], maxTools: number, getName: (t: T) =>
string | undefined): T[]`. `convertTools` and `clampRequestForProvider` both
call it (the only difference today is the name accessor — `t.function.name`
vs `t.function?.name`, unified via `getName`). No behavior change.

### C. `stream.ts` — disconnect fix + typing (findings 3, 8)

- Replace the `finally`-block `send(...)` closing-event calls with `safeSend`
  so a disconnected client (whose `controller.enqueue` throws) does not abort
  the function before `stats` is assigned — eliminating the phantom
  `stream_error` log on normal client cancels.
- Add `contentIndex: number` to the `toolBlocks` map value type and assign it
  at block creation; delete the three `(tb as any)._contentIndex` casts.
- Use `sseEvent` from `sse.ts`.

### D. `webSearch.ts` — dedupe (finding 2 + result block)

- Use `sseEvent` from `sse.ts`.
- Extract `buildWebSearchResultBlock(hits, failed)` — the
  `web_search_tool_result` / `…_error` content currently built identically in
  `writeSyntheticWebSearchStream` and `buildSyntheticWebSearchResponse`.

### E. `server.ts` — error helpers + envelope normalization (findings 4, 9)

- Use `errorEventStreamResponse` for the all-providers-failed streaming path.
- **Normalize the 3 unwrapped JSON error sites** (`invalid JSON` L275,
  provider-resolve L338, conversion-error L373) to `jsonError(...)` so they
  emit `{type:'error', error:{type:'invalid_request_error', message}}`. This
  is the only intentional wire-shape change; it makes the CLI surface the real
  message instead of a generic SDK fallback. The already-wrapped sites (auth,
  all-failed, non-JSON-upstream) also move to the helper with their **exact
  existing** shape preserved.
- Preserve all uncommitted inbound-auth logic already in the working tree.

### F. `anthropicPassthrough.ts` — error helper (findings 4, 5)

Use `errorEventStreamResponse` for the unreachable streaming path and
`SSE_HEADERS` for the success stream headers.

### G. `logger.ts` — size-based rotation (finding 6)

Seed an in-process byte counter from `statSync(LOG_PATH)` at module load;
increment by each appended line's byte length. When it crosses
`JARVIS_PROXY_LOG_MAX_BYTES` (default **10 MB**), rotate `proxy.log →
proxy.log.1` (single archive; bounds disk to ~2× cap) and reset the counter.
Stays fire-and-forget and best-effort (single-writer in practice — port 4000
is exclusive; launchers kill the stale proxy before respawn). Rotation
failures are swallowed like existing write failures.

### H. `reasoning-cache.ts` — true LRU (finding 7)

On a `getReasoning` hit, re-insert the key (`cache.delete` + `cache.set` with
the existing entry) so `Map` insertion order tracks recency-of-use and
`keys().next().value` evicts the genuine LRU entry. TTL/persistence unchanged.

### I. Adjacent cruft (findings 10, 11)

- `package.json`: `"test": "bun test src/proxy"` (replaces the dead `echo`).
- `rm` the untracked `scripts/jarvis-proxy.log` + `.err.log`.
- Add `*.log` to `src/cli/.gitignore` so proxy logs never get committed.

### J. Direct-mode SSE streaming — Bun latency upgrade (finding 12)

Every SSE `Response` in the proxy is built with the **default** `ReadableStream`
+ `controller.enqueue()`. Bun batches enqueued chunks rather than flushing each
immediately ([Bun #13923](https://github.com/oven-sh/bun/discussions/13923)),
adding latency to token streaming. Switch the per-token streaming paths to a
**direct-mode** stream (`new ReadableStream({ type: "direct", … })`) and replace
`enqueue(...)` with `controller.write(...)` + `await controller.flush()` so each
Anthropic SSE event leaves the socket as soon as it's produced.

Scope: `stream.ts::convertOpenAIStreamToAnthropic`, `anthropicPassthrough.ts`
(streaming branch), `webSearch.ts::writeSyntheticWebSearchStream`, and the
streaming-error path. The `sse.ts` helpers (§A) centralize write/flush so each
call site stays a one-liner. **Bytes are identical** — only flush *timing*
changes; the `event:`/`data:` sequence is unchanged. Composes with §C — the same
`safeSend` wrapper guards `controller.write` in the `finally` block.

Risk: low-moderate — Bun-specific controller API; covered by the
streaming-sequence test (§Testing) that asserts the exact event order.
Independently revertible (restore the default-mode `start(controller)` shape
per file).

## Testing

Baseline: `bun test src/proxy` → 142 pass. The suite grows with:

- `sse.test.ts` — `sseEvent` output, `SSE_HEADERS` content,
  `errorEventStreamResponse` body shape, `jsonError` envelope.
- `convert.test.ts` (extend) — `truncateToolsByPriority` keeps priority tools
  and respects the cap; parity with the previous inline behavior.
- `stream.test.ts` (new or extend) — a controller whose `enqueue` throws
  after first use does **not** throw out of `convertOpenAIStreamToAnthropic`
  (the disconnect fix); `contentIndex` typing compiles.
- `logger.test.ts` (extend) — rotation triggers when the byte counter crosses
  a low test cap; `proxy.log.1` appears; counter resets.
- `reasoning-cache.test.ts` (new) — at `MAX_ENTRIES`, a `getReasoning` on an
  old key before inserting a new one spares it from eviction (LRU).
- `proxy-bugfixes.test.ts` (extend) — the 3 normalized error responses carry
  the `{type:'error', error:{type, message}}` envelope.
- `stream.test.ts` (extend, §J) — under direct-mode the converted stream still
  emits the same `message_start → … → message_stop` event order; a
  `controller.write` that throws mid-stream (disconnect) does not abort the
  function (shares the §C `safeSend` assertion).

## Verification & risk

- `bun test src/proxy` green and grown.
- `bun build <file> --no-bundle` parse check on each touched file (bare `tsc`
  floods false errors on this compiled-artifact tree — per project memory).
- Risk: **low**. No success-path wire change; the only error-path wire change
  is the intentional envelope normalization (finding 9), covered by a test.
  Internal-only behavior changes: on-disk log rotation, cache eviction order,
  and SSE flush *timing* (§J — direct mode; identical bytes/event order).
  `server.ts` edits coexist with the in-tree inbound-auth diff.

## Optional / deferred enhancements (out of default scope)

Surfaced by the documentation research; **not** in the default implementation —
listed so they're captured for a future pass.

- **Provider cooldown / circuit-breaker** in `server.ts::executeWithFallback`.
  Today the fallback chain is re-tried from scratch every request; a hard-down
  provider is re-attempted (after retry exhaustion) on every turn. Mainstream
  gateways mark a failing target unhealthy for a cooldown window and skip it
  ([LiteLLM](https://docs.litellm.ai/docs/proxy/reliability),
  [Portkey](https://portkey.ai/docs/product/ai-gateway/circuit-breaker)). This
  is genuinely new stateful behavior (a change to fallback policy), so it's
  deferred rather than folded into the "targeted" tier. If pursued: a small
  in-process `Map<providerName, cooldownUntil>` checked at the top of the chain
  loop, an env kill-switch, plus trip + recovery tests.

## References

Documentation that informs or validates each area (gathered 2026-06-15).

- **Multi-provider gateway patterns** (`executeWithFallback`, `retry.ts`):
  [LiteLLM reliability/fallbacks](https://docs.litellm.ai/docs/proxy/reliability),
  [LiteLLM routing](https://docs.litellm.ai/docs/routing-load-balancing),
  [Portkey retries/fallbacks/circuit-breakers](https://portkey.ai/blog/retries-fallbacks-and-circuit-breakers-in-llm-apps/),
  [Portkey circuit-breaker](https://portkey.ai/docs/product/ai-gateway/circuit-breaker),
  [Portkey gateway (MIT)](https://github.com/Portkey-AI/gateway).
- **Backoff + jitter** (`retry.ts` — validates the existing full-jitter impl):
  [AWS Exponential Backoff and Jitter](https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/),
  [AWS Well-Architected REL05-BP03](https://docs.aws.amazon.com/wellarchitected/latest/framework/rel_mitigate_interaction_failure_limit_retries.html).
- **Bun streaming** (§J): [Bun Streams](https://bun.sh/docs/runtime/streams),
  [Bun #13923 — default ReadableStream batches](https://github.com/oven-sh/bun/discussions/13923).
- **SSE wire format** (`sse.ts`):
  [MDN Using SSE](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events),
  [WHATWG SSE spec](https://html.spec.whatwg.org/multipage/server-sent-events.html).
- **Anthropic Messages API** (client shape — validates finding 9 + `stream.ts` events):
  [Streaming](https://platform.claude.com/docs/en/build-with-claude/streaming),
  [Errors](https://platform.claude.com/docs/en/api/errors.md).
- **OpenAI Chat Completions** (upstream shape — `convert.ts`, `stream.ts`):
  [Streaming events](https://developers.openai.com/api/reference/resources/chat/subresources/completions/streaming-events).
- **DeepSeek** (validates `reasoning-cache.ts` + `logger.ts` cache stats):
  [Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode),
  [Context Caching](https://api-docs.deepseek.com/guides/kv_cache).
- **JWT** (validates `proxyJwt.ts`):
  [RFC 8725 BCP](https://datatracker.ietf.org/doc/html/rfc8725).

## Rollback

All changes are within `src/cli/src/proxy/` plus `package.json` /
`.gitignore`; revert the commit. The new `sse.ts` is additive; deleting it and
restoring the inline helpers fully reverts section A. §J (direct-mode streaming)
is per-file revertible to the default-mode `start(controller)` shape.
