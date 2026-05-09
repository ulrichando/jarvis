# Memory Consolidator — design spec (2026-05-08)

> Architectural sibling to `pipeline/memory_extractor.py`. Inspired by Claude Code's `services/autoDream/` consolidation pattern (per the leak review). Trigger differs: threshold-based instead of daily.

## Why

Today the v2 memory layer extracts on every user turn and writes to `~/.jarvis/hub/state.db::memories`. **Nothing dedupes or merges.** The store grows monotonically; near-duplicate phrasings of the same fact ("My wife is Lizzy", "Ulrich's wife is named Lizzy", "wife: Lizzy") all coexist. Recall surfaces them as separate hits, recent-turn re-extraction reinforces them, and the supervisor's `chat_ctx` ends up bloated with the same fact in three voices.

Live count today: 35 memories total. Manual inspection shows ≥3 plausible dup clusters already. With proactive capture cranked up by the 2026-05-08 prompt changes, the rate accelerates.

## Scope

In scope:
- Detect near-duplicate / closely-related memory entries within a single category.
- Replace each detected cluster with one canonical merged entry; remove the cluster's source members.
- Trigger automatically after every Nth successful extraction (default N=10).
- Run safely in JARVIS's single-asyncio-event-loop voice-agent runtime.

Out of scope (explicitly):
- Conflict supersession (newer fact contradicts older). Higher risk; deferred.
- Cross-category clustering. Real-world rate is near zero; runs per-category.
- Time-decay pruning. Memories don't expire by age in v2; they expire by being superseded or merged.
- Voice-side confirmation loops. Approach B from brainstorming was rejected for voice friction.
- Schema changes / new tables. All writes go through the existing `_publish_event_async("memory.value.{upserted,removed}")` path.

## Non-goals (anti-scope)

- This is not an embedding service.
- This is not a knowledge-graph ontology.
- This is not the place to fix the few-shot prompt of the per-turn extractor.
- This does not generate user-visible voiced messages. It is silent, off-band.

## Architecture

### File layout

New file: `src/voice-agent/pipeline/memory_consolidator.py` (~250 lines).

- Mirrors the shape of `memory_extractor.py`: pure async functions, module-global state for single-event-loop concurrency control, env-var disable, structured logging.
- Tests: `src/voice-agent/tests/test_memory_consolidator_2026_05_08.py`.

### Trigger

`extract_memory_from_turn` (in `memory_extractor.py`) is the only caller. After a successful parse + publish, the extractor:

1. Increments a module-global `_EXTRACTIONS_SINCE_LAST_CONSOLIDATE: int` (lives in `memory_consolidator.py`, exposed via `record_extraction()`).
2. If the count reaches `_CONSOLIDATE_EVERY_N` (default 10, env: `JARVIS_MEMORY_CONSOLIDATE_EVERY_N`), schedules `consolidate_all_categories()` via `asyncio.create_task` (fire-and-forget).
3. Resets the counter.

The consolidator is OFF by default if `JARVIS_MEMORY_CONSOLIDATOR=0` (mirrors the `JARVIS_CONFAB_DETECTOR` flag pattern). On 2026-05-08 we ship with it ON; the kill-switch exists for one-line live disable.

### Concurrency guard

Module global `_CONSOLIDATION_IN_FLIGHT: bool`. Read-and-set is single-event-loop atomic. If a second trigger fires while one is in flight, the second is dropped (logged as a skip). The dropped trigger doesn't lose data — the memory store is the source of truth and the next trigger picks up where this one left off.

### Per-category fanout

`consolidate_all_categories()` runs each of `("user", "feedback", "project", "reference")` sequentially (not in parallel — limits LLM concurrency to 1 to keep blast radius small). For each category:

1. **Gather:** read all rows from `state.db.memories WHERE category = ?`. Filter out memories younger than `_YOUNG_EXCLUSION_SECONDS` (default 300 = 5 min). If the result has < 2 entries, skip the category.
2. **LLM call:** `consolidate_category(category, entries)` builds a small prompt and hits llama-3.1-8b-instant via Groq. Same model + endpoint as the extractor.
3. **Validate:** every cluster's members must be IDs present in the input; canonical content must be non-empty, ≤ `_MAX_CONTENT_CHARS` (= 500, same cap as extractor), must NOT match `_META_PARAPHRASE_RE` (imported from `memory_extractor`); each cluster must have ≥ 2 members (a 1-member cluster is a no-op and we don't write it).
4. **Apply:** for each valid cluster, in order:
   - Generate a fresh `memory_id` for the canonical (uses `tools.memory._memory_id`).
   - `await _publish_event_async("memory.value.upserted", { memory_id, content, category })`.
   - For each member ID: `await _publish_event_async("memory.value.removed", { memory_id })`.
5. **On any failure** (LLM error, JSON parse fail, validation fail, publish exception): log + early-return for the category. **Never partial-apply.** Each cluster is its own atomic apply, so a mid-fanout failure is bounded to one cluster's writes already on disk.

### Prompt shape

```
You are a memory consolidator. Group near-duplicate or
closely-related entries into clusters of 2+; produce ONE
canonical merged content per cluster.

Rules:
- Cluster only entries that state the same fact about the same
  subject. Different facts about the same subject (e.g. wife's
  name vs wife's profession) stay separate.
- Canonical content must be a single first-person, declarative
  sentence (max 500 chars). NO narration shapes ("the user
  appears to…"); NO hedge ("seems to be…").
- Output JSON ONLY: {"clusters": [{"members": [...ids...],
  "canonical": "..."}]}. If nothing to merge, output {"clusters": []}.

Entries:
- {id1}: {content1}
- {id2}: {content2}
…
```

Few-shot block: 2 cluster examples + 1 "no-merge" example. Calibrated against the live failure shape "wife=Lizzy" repeated.

### Telemetry

Log-only for v1 (no new SQL table). Each consolidation event emits a structured INFO line:

```
[consolidator] category=user candidates=12 clusters=2 merged_into=2 removed=5 elapsed_ms=410
```

Errors emit WARNING with the category and exception type. (A future v2 can persist to `~/.local/share/jarvis/turn_telemetry.db` if useful.)

### Failure modes — design for correctness not for catching bugs

- **LLM returns garbage / non-JSON** → log, skip category. Memory store untouched.
- **LLM returns valid JSON but member IDs don't match input** → log, skip cluster. Other clusters in the same category are still applied if valid.
- **`_publish_event_async` raises mid-cluster** → log, skip remaining clusters in that category. Already-published clusters stay (the publish path is the source of truth).
- **Concurrent trigger** → second is dropped. Counter resets but the missed work is implicit — the next trigger will see the un-merged entries again.
- **Empty / single-entry category** → no-op, no LLM call.
- **All entries in a category are < 5 min old** → no-op for that category.
- **Disabled via env** → `record_extraction()` is a cheap no-op; no LLM, no DB read.

### Idempotency

A second run on the same memory store after a successful first run sees the canonical entries (not the now-removed members) and finds nothing to merge. Idempotent by construction.

## Components — boundary clarity

| Component | Responsibility | Inputs | Outputs |
|:--|:--|:--|:--|
| `record_extraction()` | Counter + threshold trigger. Called from the extractor's success path. | (none) | bool: did it schedule a consolidation? |
| `consolidate_all_categories()` | Top-level entry; fans out to per-category. | (none — reads global env / DB) | None (side effect: publish events + log) |
| `consolidate_category(category, entries)` | Single-category LLM + validate + apply. Pure-ish (DB writes via injected publisher). | category name, list of (memory_id, content, created_at) | None |
| `_call_consolidator_llm(category, entries)` | LLM call seam — monkeypatched in tests. | same | raw text response |
| `parse_consolidator_output(raw, valid_ids, valid_category)` | Pure parser + validator. | raw LLM text + ID set + category | list[Cluster] or [] |

## Tests (TDD: write these first)

`test_memory_consolidator_2026_05_08.py`:

1. `test_disabled_when_env_zero` — `JARVIS_MEMORY_CONSOLIDATOR=0` makes `record_extraction()` a no-op even past threshold.
2. `test_record_extraction_increments_until_threshold` — counter + reset.
3. `test_skips_concurrent_consolidation` — `_CONSOLIDATION_IN_FLIGHT=True` makes the second `consolidate_all_categories` a no-op.
4. `test_skips_young_memories` — entry < 5 min old is excluded from the candidate set.
5. `test_skips_category_with_zero_or_one_entry` — no LLM call.
6. `test_parse_valid_clusters` — happy path returns the expected `Cluster` list.
7. `test_parse_rejects_unknown_member_id` — cluster referencing an ID not in input is dropped.
8. `test_parse_rejects_meta_paraphrase_canonical` — canonical that matches `_META_PARAPHRASE_RE` is dropped.
9. `test_parse_rejects_oversize_canonical` — > 500 chars dropped.
10. `test_parse_rejects_singleton_cluster` — 1-member cluster dropped (no-op).
11. `test_parse_rejects_garbage_input` — non-JSON / fragment → empty list.
12. `test_apply_publishes_upsert_then_remove` — order matters (canonical before removes).
13. `test_apply_skips_remaining_on_publish_exception` — bounded blast radius.
14. `test_idempotent` — second run on same store is a no-op.

All tests use a `FakePublisher` and a monkeypatched `_call_consolidator_llm` so no Groq/HTTP/DB.

## Configuration

| Env var | Default | Effect |
|:--|:--|:--|
| `JARVIS_MEMORY_CONSOLIDATOR` | `1` | `0` disables entirely (record_extraction returns immediately). |
| `JARVIS_MEMORY_CONSOLIDATE_EVERY_N` | `10` | Threshold for triggering. |
| `JARVIS_MEMORY_CONSOLIDATE_YOUNG_EXCLUSION_S` | `300` | Memories younger than this many seconds are excluded from candidates. |

Read at runtime (not module-import-cached) — same pattern as the 2026-05-08 specialist gate fix.

## Risks / open questions

- **LLM cost:** one call per N=10 extractions × four categories = ~1 small-model call per 2.5 extractions when amortized. With current ≤1-2 extractions/min during active conversation, that's ~30-50 calls/hour at peak. Acceptable on Groq's free / paid tier.
- **Wrong merges:** the canonical content carries forward only what the LLM included. If the LLM merges "wife=Lizzy" + "wife went to MIT" into "wife=Lizzy went to MIT", that's actually correct merging. If it drops "went to MIT", that's a regression — but the original entries are removed too. Mitigation: the next time the user mentions MIT, the per-turn extractor re-captures it. Self-healing.
- **Schema drift:** state.db.memories has a `category` column; we rely on it being one of the 4 known values. If a stray category lands, it just gets skipped (consolidate runs only over the known 4).
- **Timing of removes:** if recall fires between the upsert and the removes, recall briefly returns both old and new. Tiny window; acceptable.

## Out-of-scope follow-ups (don't do these now)

- Periodic timer trigger (in addition to threshold) — useful for sessions that extract heavily then go idle for hours.
- Cross-category clustering — only worth it if telemetry shows it happening.
- Supersession / contradiction detection — separate design, separate failure modes.
- Voice-confirmed dry-run mode (Approach B from brainstorming) — easy to add later via an `if dry_run:` branch in the apply path.
- Persisted telemetry table for consolidation events.

## Implementation order

The implementation plan (separate `writing-plans` artifact) will TDD this in order:

1. Pure parser + validator (`parse_consolidator_output`) and its tests.
2. `consolidate_category` with a fake publisher and a monkeypatched LLM.
3. `consolidate_all_categories` (fanout + concurrency guard).
4. `record_extraction` (counter + threshold + scheduler).
5. Wire it into `extract_memory_from_turn` after the existing `_mark_extraction_success`.
6. Update CLAUDE.md to document the new module + env vars.

End state: ~250 lines of new module code + ~250 lines of test code + a few-line edit to `memory_extractor.py` + 4 lines in CLAUDE.md.
