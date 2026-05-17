# JARVIS Memory System — Round 2 Deep Review

**Reviewer:** Claude (Opus 4.7 / 1M ctx)
**Date:** 2026-05-16
**Round 1 verdict:** 4-layer design (extractor + force-recall + denial-suppressor + consolidator) is sound. Round 2 drills into **quality**, not architecture.

---

## TL;DR (top 5, in severity order)

1. **The memory store is polluted with garbage.** Live snapshot: 99 memories total; 20+ are `Coding Kiddos`-prefixed fictional / TV-narration artifacts ("Coding Kiddos is under Courage", "Coding Kiddos has a strict no-phones policy on the school bus", "Coding Kiddos charges $2000 for response"). The extractor's few-shot example brand name is being copied as a default prefix when the LLM has no real subject to bind to. **P0.** Root cause: prompt anchoring effect + no semantic validation that "extracted fact" actually came from the transcript.
2. **`memory_auto_extracted` telemetry is dead.** Schema says it's there; live data shows 0/163 turns ever flagged. Fire-and-forget `asyncio.create_task(_run_extractor_and_publish(text))` in `jarvis_agent.py:3539` never writes back to telemetry — so the dashboard rubric "≥80% of conversations get at least one auto-extracted memory" is unmeasurable. **P0** for operability.
3. **Two-source `JARVIS_MEMORY_TOP_N` divergence.** `pipeline/config.py:242` defaults to **8**; `tools/memory.py:393` reads the same env var with default **30**. `format_memories_for_prompt()` reads the latter — so the prompt actually injects 30 memories, not 8. Combined with finding #1 this means up to 30 fictional facts are seeded into the supervisor every turn. **P0.**
4. **Few-shot examples are 100% Coding Kiddos / Pretva / Lizzy.** Round 1's "skewed" flag is confirmed and is causally responsible for the pollution above. The LLM mimics the surface form of the examples — "Coding Kiddos charges $X" / "Ulrich runs Y" — even when transcripts have no such grounding. **P0** (drives #1).
5. **Recall is pure SQLite `LIKE` substring grep.** No embeddings, no semantic search, no rank-by-recency-and-frequency. `recall_conversation()` in `jarvis_agent.py:1748` does a `LIKE '%query%'` against the raw transcript log; `tools/memory_recall.py` adds tokenization but is still substring AND. Works for proper nouns ("Lizzy", "Pretva"). Fails on conceptual / fuzzy recall ("what did I say about pricing?" misses "$600/6mo" if user never used the word "pricing"). **P1** — defer until #1-#4 are fixed; pollution is a worse problem than retrieval precision.

---

## Architecture diagram

```
┌──────────────────────────────────────────────────────────────────────┐
│ USER TURN BOUNDARY                                                   │
│ (jarvis_agent.py::on_user_turn_completed, line 3326)                 │
└─────────────────────────────┬────────────────────────────────────────┘
                              │
              ┌───────────────┼───────────────────────────┐
              ▼               ▼                           ▼
  ┌───────────────────┐ ┌───────────────────┐ ┌──────────────────────┐
  │ Layer 1 (LLM)     │ │ Layer 1.5 (regex) │ │ Layer 2: is_recall   │
  │ extractor         │ │ capture-trigger   │ │ → force tool_choice  │
  │ llama-3.1-8b      │ │ "we charge X" /   │ │   on recall_conv     │
  │ 5s timeout,       │ │ "I run Y" / etc.  │ │ (turn_dispatcher.py  │
  │ Groq, T=0.0       │ │ FIRE&FORGET       │ │  :136-145)           │
  │ max_tokens=160    │ │                   │ │                      │
  │ FIRE & FORGET     │ │                   │ │ Persists across turns│
  │ (no telemetry)    │ │                   │ │ unless reset (#4671) │
  └────────┬──────────┘ └────────┬──────────┘ └──────────┬───────────┘
           │                     │                       │
           ▼                     ▼                       ▼
  ┌──────────────────────────────────────┐    ┌─────────────────────┐
  │ _publish_event_async                  │    │ Supervisor LLM      │
  │ ("memory.value.upserted", payload)    │    │ (Anthropic Sonnet   │
  │  → Redis streams (events:memory)      │    │  4.6 + fallbacks)   │
  └────────┬─────────────────────────────┘    │                     │
           ▼                                   │  format_memories_   │
  ┌──────────────────────────────────────┐    │  for_prompt()       │
  │ hub/server.py consumer                │    │  injects up to 30  │
  │ INSERT … ON CONFLICT memory_id       │←───┤  memories per turn  │
  │  → ~/.jarvis/hub/state.db.memories    │    │  (with age + stale  │
  │     (sha256 of normalized content)    │    │   reminder)         │
  └────────┬─────────────────────────────┘    └──────────┬──────────┘
           │                                              │
           │              ┌───────────────────────────────┘
           │              ▼
           │   ┌──────────────────────────────────────┐
           │   │ Layer 3: denial_detector              │
           │   │ (sanitizers/denial_detector.py)       │
           │   │ Patches LLMStream._parse_choice       │
           │   │ buffers content[-400], on capability- │
           │   │ denial regex match BLANKS the chunk    │
           │   │ (does NOT re-roll yet — see comments)  │
           │   └──────────┬───────────────────────────┘
           │              ▼
           │   ┌──────────────────────────────────────┐
           │   │ TTS / confab_detector at write-time   │
           │   │ (gates persistence of assistant turn) │
           │   └───────────────────────────────────────┘
           │
           ▼
  ┌──────────────────────────────────────┐
  │ Every Nth (default 10) successful     │
  │ extraction → consolidate_all_         │
  │ categories scheduled via              │
  │ asyncio.create_task                   │
  │                                       │
  │ Per category: read up to 200, drop    │
  │ <300s young, llama-3.1-8b returns     │
  │ JSON {"clusters":[…]} of dup-merges,  │
  │ validate, publish upsert/remove pairs │
  └───────────────────────────────────────┘
```

### Storage entities

| Table / file | Purpose | Schema highlights | Notes |
|---|---|---|---|
| `~/.jarvis/hub/state.db::memories` | Durable user-facts | `memory_id PK (sha256), content, category, source, source_session_id, created_ts, updated_ts, last_used_ts, use_count` | Single canonical store. 99 rows live. Top-used row at 459 reads (every-turn seeding bumps `use_count`). |
| `~/.jarvis/hub/state.db::messages` | Conversation log | `id, session_id, role, text, tool_calls_json, ts (ms)` | 784 rows. Used by `recall_conversation()` LIKE-search and by `seed_chat_ctx()` for recent-turn pre-load. |
| `~/.local/share/jarvis/turn_telemetry.db::turns` | Per-turn telemetry | adds `memory_auto_extracted INTEGER DEFAULT 0` | The column is wired in schema but never set at runtime — sum is 0 across 163 rows. |
| `~/.jarvis/learned_rules.md` | `remember_this()` rule append-log | Plain markdown bullets | Separate from `memories` table; used by `remember_this()` (a different surface from `remember()`). |

---

## Findings per area

### 1. Extraction quality

**File:** `src/voice-agent/pipeline/memory_extractor.py:182-235` (`_EXTRACTOR_PROMPT`)

Round 1's flag is confirmed and **causal**, not cosmetic. Five positive examples:

- `we charge $600 for 6 months` → "Coding Kiddos charges $600 for 6 months ($100/mo) per student."
- `my wife's name is Lizzy` → "Ulrich's wife is named Lizzy."
- `we teach python javascript and lua` → "Coding Kiddos curriculum covers Python, JavaScript, and Lua."
- `i run pretva` → "Ulrich runs Pretva, a ride-hailing service in Cameroon."
- `every time i ask jarvis to remember he says he can't` → "User reports JARVIS denies its own memory…" (with Why/How-to-apply)

Three of five positives output the brand name **Coding Kiddos**. The 8B extractor LLM has no theory of mind; it sees three examples that all start with "Coding Kiddos …" and one that starts with "Ulrich …" and learns "stable fact = Coding Kiddos / Ulrich predicate". When the actual transcript contains an unrelated nominal subject ("the device", "the third order", "Mr. Hound") the LLM **prefixes Coding Kiddos** by surface analogy. Live evidence in `state.db`:

```
project|Coding Kiddos appears to be referencing a global state.
project|Coding Kiddos has a day in their curriculum about a baby's bed.
project|Coding Kiddos may offer outdoor activities with ice.
project|Coding Kiddos has a strict no-phones policy on the school bus.
project|Coding Kiddos parent is being asked about the number of years.
```

These are almost certainly TV / Roblox / fiction narration that was extracted because the user happened to be near a speaker. Once `Coding Kiddos` is in the store, **the per-turn `format_memories_for_prompt()` re-injects them**, and the supervisor LLM faithfully sees the brand 20+ times every prompt — locking in the contamination loop. Memory consolidator can't help here: these are 20 different sentences, not near-duplicates.

**Proposed replacement examples** — 4 categories × 1-2 each, diverse subjects, no shared proper noun across categories:

```text
USER: "my landlord is Mr. Adebayo, he lives upstairs"
OUTPUT: user: Ulrich's landlord is Mr. Adebayo, who lives upstairs.

USER: "for voice work I prefer the terse register, no 'sir' anywhere"
OUTPUT: feedback: User prefers terse register in voice replies and rejects 'sir'/butler phrasing. Why: explicit instruction. How to apply: every voice reply — drop 'sir', drop archaic openers ('Indeed', 'Quite'), keep replies under 30 words.

USER: "the JARVIS voice agent runs as a systemd user service, never restart while a session is active"
OUTPUT: project: The JARVIS voice-agent runs as a systemd --user service. Don't restart while a session is active (check turn_telemetry.db for last ts_utc within 60s before restarting).

USER: "my mother's Pretva driver-issues tracker is at /etc/pretva/issues.md"
OUTPUT: reference: Pretva driver-issue tracker is at /etc/pretva/issues.md — check before answering operational questions about drivers.

USER: "i have three teachers at coding kiddos: yvonne, marc, and theo"
OUTPUT: project: Coding Kiddos has three teachers — Yvonne, Marc, Theo.

USER: "yeah okay"
OUTPUT: SKIP

USER: "could you tell me about the history of England"
OUTPUT: SKIP

USER: "the character was hiding their emotions"
OUTPUT: SKIP
```

Critical changes:
- **Spread subjects across categories**: landlord (user), register-preference (feedback), JARVIS architecture (project), tracker location (reference). No single noun dominates.
- **Add a SKIP for "the character … emotions"** so the LLM learns to drop background-fiction narration (live failure shape).
- **Promote "feedback" example to a content-rich block** with Why/How-to-apply, matching the canonical structure documented in `tools/memory.py::remember()` docstring.

### 2. Recall accuracy

Two recall surfaces exist; both are **substring/LIKE-based**:

**A. `recall_conversation(query)` @function_tool** — `jarvis_agent.py:1748`. SQL: `SELECT ts, role, text FROM messages WHERE role IN ('user','assistant') AND lower(text) LIKE '%{query}%' ORDER BY ts DESC LIMIT 8`. No tokenization, no stopword filter. Pure substring match.

**B. `recall(query, days=30, limit=5)` @function_tool** in the `memory_recall` delegate-subagent — `tools/memory_recall.py:87`. Better: tokenizes into 3+-char non-stopword words, requires ALL to appear (`AND` of LIKEs), groups by session, time-bounds via cutoff. Still no semantic search.

**C. `format_memories_for_prompt()`** — `tools/memory.py:378`. Reads top-N memories ordered by `updated_ts DESC` (effectively recency), no relevance ranking, injects every turn. This is the **primary** seeding mechanism; the function tools are only called explicitly.

**Failure modes:**

| Failure mode | Frequency in current state | Root cause |
|---|---|---|
| Missed recall (semantic miss) | High for paraphrased queries. "what's my pricing?" misses "$600/6mo" if no literal "pricing" token. | No embeddings. |
| Wrong recall | Medium. "Coding Kiddos" injected on every turn — the supervisor often answers a tangentially-related question by referencing it. | Pollution + top-N=30 + no scoring. |
| Confabulated recall | Medium. Once a confab lands in `messages` (the conversation log) it shows up in `recall_conversation` substring hits and the LLM treats it as real history. Confab-detector guards persistence of `messages` writes but only on strong-claim shapes; quieter false statements still land. | `confab_detector._STRONG_CLAIMS` is a tight allowlist. |

**Verdict:** retrieval is currently *good enough* because the seeded `format_memories_for_prompt()` block dominates — Layers 1/1.5 do most of the recall work upstream. **Don't add embeddings yet** (P2). Cleaning the store and shrinking top-N is higher-leverage.

### 3. Consolidator behavior

**File:** `src/voice-agent/pipeline/memory_consolidator.py`

Strong points:
- **Pure-function parser** with strict validation (`parse_consolidator_output`) — invalid JSON, missing fields, member-ID-not-in-valid-ids, or meta-paraphrase canonical → `[]` (no-op, safe).
- **Young-exclusion (`_YOUNG_EXCLUSION_SECONDS=300`)** correctly prevents merging mid-conversation extractions.
- **In-flight guard** (`_CONSOLIDATION_IN_FLIGHT`) blocks reentry.
- **Lazy hub-SDK + tools.memory imports** — no module-load cycle.
- **LLM call failures degrade silently** (`return '{"clusters": []}'`) — won't eat data.

Weak points / gaps:

1. **`_apply_clusters` aborts on first publisher exception mid-cluster** but **leaves the canonical upserted while only some members removed**. Net: a partial merge can leave both the canonical AND the old members in the store. The comment ("publish path is idempotent on next run") is true for the canonical but not for the remove-pairs — a half-applied cluster will be re-clustered next round (since members still exist), produce a NEW canonical with the OLD members, and so on. **P2** — bounded loop, but creates content drift.

2. **No cap on cluster size.** A category with 50+ members all about Coding Kiddos can be clustered into one canonical, but the LLM is called with up to 200 entries × `[:200]` chars = 40 KB of context for a single 8B-instant call. Latency at the upper bound is unmeasured.

3. **The consolidator's anti-narration filter is the same `_META_PARAPHRASE_RE`** the extractor uses. Good defensively, but it doesn't help with the "Coding Kiddos prefix" pollution: those entries pass the narration regex (they're declarative, not narration-shaped), so consolidation processes them. Since each Coding Kiddos memory is a **distinct fictional fact** (school bus / no-phones / outdoor / etc.), the LLM **shouldn't** merge them — and it doesn't, correctly. The consolidator is doing exactly what it's designed to do; the **pollution can't be cleaned post-hoc**. Has to be prevented at extraction.

4. **Trigger threshold (N=10) is mismatched to extraction rate.** Live evidence: 163 turns over ~3 days, ~99 memories created. So consolidator fires roughly every 10 turns, or ~30× total. It should be running often enough; the issue is not the trigger but the content.

5. **Consolidator never deletes** memories it considers "wrong" or "ephemeral" — it can only merge. There's no `purge` path for individual rows that should never have landed.

### 4. Storage architecture

Single canonical store: `~/.jarvis/hub/state.db::memories` (sha256-keyed by normalized content). Clear separation:

- **`memories`** = durable user-facts (current top-30 injected to every supervisor prompt).
- **`messages`** = turn-by-turn transcript log (read by `recall_conversation` and `seed_chat_ctx`).
- **`learned_rules.md`** = behavioral rules from `remember_this()` (separate plain-text append-log, NOT in state.db).

**Concerns:**

1. **`learned_rules.md` is a parallel memory surface** that lives outside the hub bus, has no consolidator, no staleness, no audit tool. `remember_this()` (jarvis_agent.py:1815) appends bullets dated `YYYY-MM-DD`. The supervisor's prompt loads these on session start. Risk: rules accumulate, contradict each other, can't be deduped via the consolidator. **P1** — should be unified with the `feedback` category in `memories` table or be explicitly siloed with its own audit.

2. **Source field is always `"voice"`** — every memory was written by the voice path. The hub schema has a `source` column but it's never set to `"web"` or `"cli"` (only one session sender right now). Fine for now, becomes interesting when the desktop UI starts writing memories directly. No bug, just future-proofing.

3. **Hub `migrate_conversations.py` exists** — the old `~/.jarvis/conversations.db` is retired in favor of state.db. Recall was missed in that migration once (`recall_conversation` kept reading the empty DB until fixed). Confirms the message store consolidation is **done**, single source of truth.

### 5. Gaslighting defense

**Files:**
- `src/voice-agent/sanitizers/denial_detector.py` (output-side)
- `src/voice-agent/confab_detector.py` (write-time)
- `src/voice-agent/pipeline/turn_router.py::is_recall_query` (force-route)

`denial_detector` is **defensive only** — it blanks the chunk, doesn't re-roll. Comment on line 113-114: "*Future work: instead of just blanking, trigger a re-roll with tool_choice forced. That requires deeper LiveKit integration.*" So when a denial fires, the user hears **silence**, then has to retry. Not ideal but ZERO false-positive cost.

`confab_detector` is well-tuned: 10-msg lookback, treats `transfer_to_*` / `delegate` as evidence, treats recent auto-extraction as evidence for "saved" claims. Specifically scoped to STRONG claim shapes (`tab is open`, `posted`, `done.`).

**Gaps:**

1. **Partial-truth confabulation** isn't caught. If the supervisor says "I saved that to memory" (matches `_SAVE_CLAIM_RE`) and an extractor success landed 25s ago **but on a DIFFERENT memorable fact**, the detector grants evidence credit. The user hears "saved" believing X was saved; what was saved was Y. No defense. **P2** — would require tying claim to specific content.

2. **The denial-detector doesn't include `_ABILITY_DENIAL` variants** like "I don't keep track of" or "I don't preserve conversation history" or "Each session starts fresh for me" (without the magic phrase "new conversation"). The regex misses these. **P1** — add 3-4 more denial shapes from live log review.

3. **Denial detector blanks the chunk, doesn't replace with anything**. Combined with "next user turn retries", this is a 1-turn-of-silence UX cost per detector trigger. Telemetry would tell us how often this happens; **memory_auto_extracted = 0 across the board** means we don't know.

### 6. MEMORY.md inspiration (Claude Code's pattern)

**Reference seen:** `~/.claude/projects/-home-ulrich-Documents-Projects-ParentShield/memory/`:
- `MEMORY.md` — 4-line index pointing at the actual content files.
- `project_parentshield.md` — frontmatter `{name, description, type: project}` + structured content with **Why** / **How to apply** body.
- `project_state.md` — same frontmatter, longer content with subsections, file paths, bulleted facts.

**Comparison:**

| Dimension | Claude Code MEMORY.md | JARVIS `memories` table |
|---|---|---|
| Storage | Plain markdown files in `~/.claude/projects/<slug>/memory/` | SQLite row per fact, content TEXT |
| Schema | YAML frontmatter (`name`, `description`, `type`) | `category, content, created_ts, updated_ts, use_count, source` |
| Granularity | One file per topic; rich multi-paragraph + subsections | One row per atomic fact (≤500 chars) |
| Index | `MEMORY.md` is a human-readable links file | `format_memories_for_prompt()` renders all rows by recency |
| Editing | User can `vim` a file | User says "forget X" → `forget()` tool runs query LIKE-match, deletes one row |
| Audit | Human-readable on disk | `audit_memories()` tool emits formatted report |
| Versioning | Filesystem (git if user wants) | SQLite WAL only |
| Type taxonomy | `user, feedback, project, reference` | **Same** four types (port done 2026-05-06; commit ref in `tools/memory.py:53-61`) |
| Body structure | Free-form with `**Why:** / **How to apply:**` convention | Single sentence, optionally with structured suffix if `category=feedback|project` (per `remember()` docstring) |

**Recommendation: adapt, don't adopt.** Voice users don't want to maintain markdown by hand, and the supervisor LLM benefits from atomic deduped rows for prompt injection (a 200-line markdown file would blow the prompt budget — and the rule from `2026-05-08-token-aware-pruning` already shows the budget is tight at 128k).

But three pieces of the Claude Code design **should be ported**:

1. **Per-memory `name`/`description` fields** beyond `content`. Right now the only key is sha256 of normalized content. Adding `topic` (short noun phrase, indexed) would let:
   - `format_memories_for_prompt()` group by topic for cleaner system-prompt rendering.
   - `recall_conversation()` filter by topic before substring search.
   - `forget()` accept topic as the query and remove a coherent group rather than one row.

2. **A user-facing flat-file mirror.** Generate `~/.jarvis/memory/index.md` + `memory/<category>.md` on every consolidator run. Read-only for the agent; user edits feed back via a watcher → republish path. This gives the user a `vim` surface for cleanup without an extra UI. Costs ~50 lines of code.

3. **Structured-body convention for feedback/project** is **already in the `remember()` docstring** (Why / How to apply) but isn't enforced. Add a soft-enforce on `_publish_event("memory.value.upserted", …)` — if `category in (feedback, project)` and content lacks `Why:` / `How to apply:`, log a warning and (optionally) suppress the write. Or have the extractor's prompt emit them. The current extractor's feedback example does emit them; project examples don't.

### 7. Cost / latency

**Extraction cost** — `_call_extractor_llm`:
- Model: `llama-3.1-8b-instant` (Groq)
- `max_tokens=160`, `temperature=0.0`, `timeout=5.0s`
- Prompt: ~1.2KB few-shot template + transcript (typ. 30-80 chars). ~350-400 tokens in.
- Output: 1 line, ≤160 tokens. Usually <50.

At Groq pricing (~$0.05/M input, $0.08/M output for 8b-instant): ~$0.00002 per turn. 163 turns over 3 days = ~$0.003. **Functionally free.**

**Latency** — `asyncio.create_task(...)` in `jarvis_agent.py:3539` makes the extractor non-blocking. Empirically the 5s timeout caps the worst case; typical Groq 8B is 60-150ms. Supervisor LLM call runs in parallel. **Zero added critical-path latency.**

**Filters / no-fire paths:**
- `not transcript or not transcript.strip()` → skip
- `os.environ.get("GROQ_API_KEY")` missing → skip (returns SKIP synthetically)
- `EXTRACTOR_SKIP` returned by LLM → no publish
- `_VALID_CATEGORIES` mismatch / over-length / `_META_PARAPHRASE_RE` match → no publish

**Consolidator cost** — ~10× the extractor input size (200 rows × ~150 chars each = 30KB), called every Nth (default 10) extraction. ~$0.0002 per fire. **Also functionally free.**

### 8. What's missing

| Missing capability | Cost-benefit | Priority |
|---|---|---|
| Semantic embeddings for recall | High effort (storage + sync + dimension choice + model picker). Marginal benefit until pollution is fixed. | **P2 — defer** |
| User-controlled deletion UI | `forget(query)` tool exists but is voice-only. Desktop Tauri has no memory pane. Web has none. | **P1** — Tauri pane already has settings; add a memories pane |
| Memory expiration / TTL | The `_STALE_DAYS=30` system-reminder warns the LLM but never deletes. With 99 rows growing, garbage compounds. | **P1** — auto-prune memories that haven't been `use_count`-bumped in 60+ days |
| Cross-session contradictions | Two memories saying "I run Pretva" and "Pretva is sold to John" coexist with no merge. Consolidator merges DUPLICATES, not CONTRADICTIONS. | **P2** — needs a separate `detect_contradiction` LLM pass |
| Backup / export | No `jarvis memories export > file.md` CLI. If state.db corrupts, everything is gone. | **P1** — trivial to add |
| Source-pinning on recall hits | When recall returns "Ulrich's wife is Lizzy", supervisor can't see WHEN that was learned. Helps with "do you still remember…" follow-ups. | **P2** — add `· first stated 2026-05-04` to bullet rendering |

---

## Severity-tagged actions

### P0 (do now, blocks user trust)

- **A1. Replace few-shot examples in `pipeline/memory_extractor.py::_EXTRACTOR_PROMPT`** with the 8-example set proposed in §1. Critically: drop all 3 Coding Kiddos positive examples. Keep "Lizzy" (it's a real fact), keep "Pretva" (real), add user/feedback/project/reference one each with **different** subjects.
- **A2. Purge the 20+ contaminated Coding Kiddos memories** from state.db. SQL: ``DELETE FROM memories WHERE content LIKE '%Coding Kiddos%' AND content NOT IN ('Coding Kiddos charges $600 for 6 months.', 'Coding Kiddos curriculum covers Python, JavaScript, and Lua.')`` — keep only the two that match the few-shot positives, drop the rest. Or **drop the whole memory store** and start clean from the new extractor.
- **A3. Wire `memory_auto_extracted=True` into `log_turn` from the on_user_turn_completed handler**. Add a session-scoped flag set in `_run_extractor_and_publish` on success; read it when telemetry writes. Without this, the design's success criterion ("≥80% of conversations with stable facts get an extraction") is unmeasurable.
- **A4. Unify `JARVIS_MEMORY_TOP_N` defaults**. Pick one — recommend **8** (matches `pipeline/config.py`, current prompt-budget reality). Update `tools/memory.py:393` to `os.environ.get("JARVIS_MEMORY_TOP_N", "8")`. With 99 polluted memories injected at top-N=30, the supervisor's prompt is currently 60% memory bullets.

### P1 (next week)

- **B1. Expand `_DENIAL_RE` patterns** to cover "I don't keep track of", "I don't preserve", "Each session starts fresh". Add unit tests with each phrase.
- **B2. Memory audit pane in desktop-tauri**. Read-only initially; bottom-right of settings. Bullet list grouped by category with a delete button per row → calls `forget()` via hub bus.
- **B3. Memory TTL pruner**. Add a `JARVIS_MEMORY_TTL_DAYS=90` env var. On consolidator run, drop memories with `last_used_ts < now - TTL` and `use_count < 5`. Quiet, low-risk.
- **B4. Unify `learned_rules.md` with `feedback` category memories**. Phase: `remember_this()` continues to write to the markdown file AND publishes `memory.value.upserted` with `category=feedback`. After 30 days, remove the markdown path; supervisor reads from the consolidated store.
- **B5. Add structured-body soft-enforce**. When `category in (feedback, project)` and content has no `Why:` / `How to apply:`, log warning, don't reject. Surface in `audit_memories()` report as "soft-violations" so the user can ask JARVIS to enrich them.

### P2 (future, after P0/P1)

- **C1. Add `topic` field** to memories (short noun phrase, indexed). Migrate via extractor prompt change ("output: `<category> | <topic>: <content>`"). Lets recall group by topic.
- **C2. Contradiction detector**. Periodic consolidator-shaped pass that flags pairs with high topic overlap but opposite polarity, surfaces in `audit_memories()`.
- **C3. Read-only markdown mirror**. `~/.jarvis/memory/<category>.md` regenerated on consolidator runs; user can `vim` and save → file watcher → republish events.
- **C4. Source-stamp on memory bullets**. `· first stated 2026-05-04` in `format_memories_for_prompt`.
- **C5. Semantic embeddings**, only if substring recall demonstrably misses (instrumentation needed first). `nomic-embed-text` (free, local, 137M params) → store 768-dim vectors in `memories.embedding` blob column.

---

## Comparison to Claude Code's MEMORY.md — verdict

| Claude Code design | JARVIS equivalent | Verdict |
|---|---|---|
| 4-type taxonomy `user/feedback/project/reference` | **Already ported** 2026-05-06 (`tools/memory.py:55`) | Match. Keep. |
| Per-topic markdown files | SQLite rows | **Reject for primary store** (prompt-budget reasons), **adopt as user-facing mirror** (read-only export) |
| YAML frontmatter `name, description, type` | None (only `category` + `content`) | **Adopt `topic`** (the `name` equivalent); skip `description` (it's the content) |
| Index file (MEMORY.md) | `format_memories_for_prompt()` | Different surfaces. Add a `memories/index.md` for human browsing alongside. |
| `**Why:** / **How to apply:**` body convention | Documented in `remember()` docstring but unenforced | **Adopt soft-enforce** (P1.B5) |
| Free user editing | Voice-only via `forget(query)` | **Adopt** Tauri pane + watch-and-republish markdown mirror (P1.B2 + P2.C3) |
| No consolidator | Has one | JARVIS-superior. Claude Code relies on user maintenance; JARVIS auto-merges. Keep. |
| No drift caveat | `_STALE_DAYS=30` system-reminder | JARVIS-superior. Keep. |
| No use_count | `use_count` + `last_used_ts` | JARVIS-superior. Use it for TTL pruning (P1.B3). |

**Net:** JARVIS's design is **operationally stronger** than Claude Code's (auto-extraction, consolidation, staleness, use-count). The only adoption-worthy gap is the **flat-file mirror for human editability + topic-as-first-class-field**. Both are P1-P2; both are additive.

---

## What's working well (so this doesn't read as all-negative)

- **Architecture is sound.** Round 1's verdict holds. 4-layer pipeline with off-band extraction + force-route + denial-suppress + consolidate is the right shape; it follows the Mem0/Zep production pattern documented in the design spec.
- **All sanitizers are idempotent** and survive re-import.
- **Confab detector's 10-msg lookback** correctly handles subagent handoffs.
- **`_META_PARAPHRASE_RE` shared between extractor + consolidator** prevents narration-shape pollution at both write points.
- **Cost is functionally zero.** Both LLM calls are 8B-instant on Groq; total memory subsystem runs <$0.01/day.
- **No latency on critical path.** `asyncio.create_task` everywhere; the supervisor's TTFW is unaffected.
- **Schema is migration-safe** (`ALTER TABLE ADD COLUMN`, default-zero fallbacks).
- **Test coverage is real**: `test_memory_extractor.py`, `test_memory_consolidator_2026_05_08.py`, `test_denial_detector.py`, `test_recall_consumer.py`, `test_memory_anchor.py`. 800+ tests in 25s.

The problem is **content quality**, not framework. Fix the few-shot examples + purge the polluted rows + wire telemetry + reconcile top-N config, and the system is in good shape.
