# JARVIS Self-Evolution — Design Spec

**Status:** Approved for implementation
**Date:** 2026-05-12
**Branch:** feat/ext-browser-control-v3 (or new branch — TBD at writing-plans time)
**Authors:** Claude (brainstorming) + Ulrich (decisions)

## 1. Problem statement

JARVIS has a partial self-evolution loop: `tools/log_analyzer.py` runs every 12 h, mines correction phrases ("don't do that", "stop doing"), proposes behavioral rules to `~/.jarvis/learned_rules.proposals.md`, and `~/.jarvis/learned_rules.md` (accepted rules) is hot-reloaded into the supervisor's system prompt every turn via `pipeline/turn_dispatcher.py`.

Two problems make the loop ineffective:

1. **The input source is dead.** `~/.jarvis/conversations.db` is 0 bytes with no `turns` table (since 2026-05-04). `log_analyzer._gather_evidence()` reads exclusively from this DB, gets nothing, silently returns. The 22 pending proposals dating back to 2026-04-28 are stale — no new proposals have generated in roughly a week.

2. **Review throughput is the bottleneck, not capability.** The 22 backlog accumulates because every proposal requires Ulrich's review by voice. The user has explicitly asked for full automation with safety rails: "make sure it's automated though."

This spec designs a fully automated rule-evolution loop that produces, evaluates, stages, promotes, and retires behavioral rules without per-rule human approval — while preserving a structurally-untouchable canonical persona ("anchor" tier) and providing fast rollback when automation gets it wrong.

## 2. Constraints

- **No online prompt mutation.** Voice-facing turn path must remain at current latency (Sonnet 4.6 supervisor, ~700ms TTFT). All evolution work runs offline (background tasks or batch jobs).
- **The model never edits the canonical persona.** A hand-curated `anchor` tier is git-tracked in the repo, sha256-baselined at boot, and structurally inaccessible to the auto-editor.
- **Append-only with archive.** No rule is deleted; retirement is a tier flip + section move. Full audit trail through `evolution_log.jsonl` + git history.
- **AI-native terminology.** "subagent" not "specialist" (per `feedback_ai_terminology.md`).
- **Existing hot-reload contract preserved.** `pipeline/prompt_builder.load_learned_rules()` reads bullet-prefix lines. v2 schema MUST keep that contract during cutover.
- **No new external services.** Uses existing API keys (Anthropic, DeepSeek, OpenAI, Groq) for evaluator ensemble. No vector DB, no new daemons.

## 3. Architecture overview

Three concurrent loops sit outside the user-facing turn path and feed a five-stage evaluator. The evaluator's verdict routes proposals into a 5-tier rule store. Most proposals never touch the human; a small queue of persona-flagged or core-promotion items is escalated for review via three redundant channels (voice / CLI / daily report).

### 3.1 Five-tier rule model

| Tier | Loaded? | Auto-editor write? | Source location | Example |
|---|---|---|---|---|
| `anchor` | Always (uncapped) | **Never** | `src/voice-agent/prompts/anchor_rules.md` (git-tracked) | `"Jarvis"` → `"Yes?"`; STAY-IN-SUPERVISOR |
| `core` | Always (uncapped) | Only via user-approved promotion | `~/.jarvis/learned_rules.md` `═══ CORE ═══` section | Chrome launch incantation |
| `accepted` | Always under MAX_LEARNED_RULES budget | Yes (after evaluator + 7d shadow) | `~/.jarvis/learned_rules.md` `═══ ACCEPTED ═══` section | "Yes? not Pardon?" |
| `staged` | Always, with `[STAGED]` prefix | Yes (after evaluator pass) | `~/.jarvis/learned_rules.md` `═══ STAGED ═══` section | Just-derived rule in 7-day probation |
| `archived` | Never | Yes (auto-retirement) | `~/.jarvis/learned_rules.md` `═══ ARCHIVED ═══` section | ElevenLabs-removed rules |

**`anchor` tier members (initial set):**

1. `"Jarvis"` / bare-vocative pings → reply EXACTLY `"Yes?"` (never "Pardon?", never "Yes, sir?").
2. STAY-IN-SUPERVISOR rule: conversational/ambiguous input stays in supervisor, never `transfer_to_*`.
3. No-sir-suffix: never append "sir" to any reply (drop-butler-register overhaul 2026-05-09).
4. No-protocol-shapes-as-voiced-text: never emit `task_done(...)`, `<function>...</function>`, JSON arrays as TTS.
5. Four load-bearing monkey-patches MUST stay installed (`deepseek_roundtrip`, `tool_name_sanitizer`, `AcousticTap`, `anthropic_strict_schema`).
6. Terminology: "subagent" not "specialist" anywhere user-facing.
7. Anti-mirror / anti-echo / anti-narrator: no "It seems like…" / "What you're saying is…" / "I'm not following…" openers.
8. `resume_false_interruption=False` — never flip without verifying SFU path.

### 3.2 Four producers

| Producer | Cadence | Signal | Output |
|---|---|---|---|
| A. Live capture | Per-turn, background | User correction phrase mid-conversation ("stop doing X", "don't open Y", "that's wrong") | Proposal with the immediately-prior JARVIS turn as evidence |
| B. Batch telemetry miner | Every 12 h | `turn_telemetry.db` patterns: `interrupted=1` clusters, `route_fallback=1`, `context_pressure=hard`, denial-detector trips, confab refusals, `user_followup_30s=1` cycles, correction-word matches | Proposal with ≥3 evidence turn IDs |
| C. Contradiction detector | Every 24 h | Scans existing rules for: dead environmental references (using a stale-ref LLM check against current codebase + memory), duplicates (Levenshtein ≥ 0.85), supersession chains | Archival proposal |
| D. Reinforcement tracker | Per-turn, in-process | Turns where a rule was in effect AND no negative correction followed within 30s | Increments `reinforced_at` and `reinforcing_turns` counter on the rule |

### 3.3 Five-stage evaluator

Each proposal flows through five stages. Failing any stage routes the proposal to the HITL queue with the reject reason logged. **The proposer LLM (Groq llama-3.1-8b-instant) is never in the judge panel** — published self-preference bias (arXiv:2410.21819) is real.

| Stage | What it does | Pass criterion | Reject action | Latency / cost |
|---|---|---|---|---|
| 1. Provenance | Validates evidence quality | ≥3 evidence turn IDs (batch) OR ≥1 explicit correction phrase (live); no similar rejection in last 7 days; rule text ≤200 chars | Drop silently, log to evolution_log.jsonl | ~0ms, free |
| 2. Persona-anchor protection | Detects whether rule would change identity/voice/tone/register or contradict any anchor rule | LLM-judge (Sonnet 4.6): JSON `{is_persona: bool, contradicts_anchor: bool, reason}`. Pass iff both false | Route to `learned_rules.proposals.md` as `NEEDS_REVIEW` for Ulrich | ~2s, ~$0.01 |
| 3. Replay-delta | Tests behavioral impact on historical turns | 200 turns from `turn_telemetry.db` (most recent + stratified by route). Render supervisor prompt with-rule vs without-rule, get both responses. Sonnet-as-judge (NOT the proposer) labels each pair {improved, neutral, regressed}. Pass iff `regressed == 0` AND `improved ≥ 3` AND no STAY-IN-SUPERVISOR breach in any with-rule output | Drop + log per-turn deltas | ~3 min parallel, ~$0.30 |
| 4. Behavioral red-team | Tests for over-correction | Generate 10 adversarial probes via Sonnet ("write user utterances that should NOT be blocked by this rule but might be"). Run each through supervisor-with-rule. Pass iff no probe triggers a refusal that wouldn't have happened without the rule | Drop + log triggering probe | ~1 min, ~$0.05 |
| 5. PoLL ensemble vote | Final quality gate | 3 judges from different families: Sonnet 4.6 (Anthropic) + DeepSeek-v4-pro (DeepSeek) + GPT-5 (OpenAI). Same rubric: `{aligned_with_user_pattern (1-5), generalizable (1-5), persona_safe (1-5)}`. Pass iff all 3 ≥ 4 on all axes (unanimous). Degrades to 2-of-2 if any provider's circuit breaker is open | Drop + log per-judge scores | ~30s, ~$0.20 |

**Total per proposal: ~5 min wall, ~$0.50–$1.50.** At ~5–10 proposals/week post-input-fix, weekly spend ~$5–$15.

### 3.4 Lifecycle transitions

| Transition | Trigger | Auto / HITL |
|---|---|---|
| `(none) → proposed` | Producer A/B emits | Auto |
| `proposed → staged` | Evaluator 5/5 pass | Auto |
| `proposed → NEEDS_REVIEW` | Stage 2 persona hit, or bulk-retirement guard | HITL |
| `staged → accepted` | 7 days in shadow + zero rollback events + golden-eval ≥95% reflexes / ≥85% LLM-judge | Auto |
| `accepted → core` | 30 days + ≥10 reinforcing turns + Ulrich approves | HITL (always) |
| `any → archived` | Contradiction detector / supersession | Auto (single) / HITL (bulk >5/cycle) |
| `staged → proposed` (rollback) | User correction within 5 turns of staging | Auto, with 30-day quarantine |
| `accepted → proposed` (quarantine) | 3 consecutive negative signals on the rule | Auto |

### 3.5 Safety controls (the three minimum from drift research)

1. **Immutable versioned anchor.** `anchor_rules.md` is git-tracked. Its sha256 is recorded in `learned_rules.md` frontmatter at every successful boot. Runtime sha-mismatch → fail fast with a loud error. Mirrors Anthropic Managed Agents' version pinning.
2. **Held-out canonical-response eval.** 50 prompts in `src/voice-agent/tests/golden_evolution_canonical.jsonl`: signature reflexes (Jarvis → Yes?), known regression triggers (Yeah/Okay → summarize hijack), bailout phrases, persona invariants. Required threshold for any staged-tier write: ≥95% exact-match on signature reflexes, ≥85% LLM-judge agreement on others, zero regressions vs. previous version. Nightly re-run + on every staged-tier write.
3. **Shadow-then-promote with auto-demotion.** New `staged` rules apply with `[STAGED]` prefix (supervisor treats them softer per prompt instruction). Auto-demotes on: 1-turn user correction within 5 turns, 3 consecutive negative signals, two consecutive nightly golden evals below threshold, confab-detector false-positive rate climb.

## 4. Rule schema v2 (file format)

Single file `~/.jarvis/learned_rules.md` plus `src/voice-agent/prompts/anchor_rules.md`. Markdown bullets with HTML-comment metadata. Sections per tier. Loader v2 lives at `pipeline/learned_rules_v2.py`, feature-flagged behind `JARVIS_LEARNED_RULES_V2=1`.

Existing `pipeline/prompt_builder.load_learned_rules()` is bullet-prefix-based — v2 keeps that shape, so v1 reader continues working during cutover.

```markdown
---
schema_version: 2
generated_at: 2026-05-12T07:55:00Z
anchor_baseline_sha256: 5a3f8c...
---

# JARVIS Learned Rules

## ═══ CORE ═══

- <!-- id=R-0007 tier=core created=2026-04-30 reinforced=2026-05-09 turns=[t-1841,t-2003,t-2199] supersedes=[R-0003] proposal=P-0012 evidence="never open chromium for chrome" --> When the user says "Chrome" or "Google Chrome", launch `/usr/bin/google-chrome --profile-directory="Default"`. To open N instances, add `--new-window` and invoke N times.

## ═══ ACCEPTED ═══

- <!-- id=R-0019 tier=accepted created=2026-05-09 reinforced=2026-05-09 turns=[t-2204] proposal=P-0031 evidence="Pardon? is for didn't-hear, not attention" --> When called by name, answer "Yes?" — never "Pardon?".

## ═══ STAGED ═══

- <!-- id=R-0021 tier=staged created=2026-05-11 reinforced=2026-05-11 turns=[t-2301] proposal=P-0042 evaluator={replay:0/0, redteam:0/10, poll:3/3} shadow_until=2026-05-18 --> [STAGED] Avoid mentioning Michael Jackson unless explicitly asked.

## ═══ ARCHIVED ═══

- <!-- id=R-0003 tier=archived created=2026-04-27 retired=2026-04-30 superseded_by=R-0007 reason=duplicate --> "Google Chrome" means `/usr/bin/google-chrome`.
- <!-- id=R-0011 tier=archived created=2026-04-27 retired=2026-05-01 reason=dead_subsystem --> Add ElevenLabs as an extra backup for speech synthesis.
```

Anchor file at `src/voice-agent/prompts/anchor_rules.md`:

```markdown
---
schema_version: 2
generated_at: 2026-05-12T07:55:00Z
this_file_sha256: 5a3f8c...   # populated by pre-commit hook; consumed by runtime
---

## ═══ ANCHOR ═══

- <!-- id=A-0001 tier=anchor --> Bare-vocative "Jarvis" pings reply EXACTLY "Yes?" — never "Pardon?", never "Yes, sir?".
- <!-- id=A-0002 tier=anchor --> STAY-IN-SUPERVISOR: conversational/ambiguous input never triggers transfer_to_*.
... (etc.)
```

## 5. Components / file layout

| File | Purpose | New / Modified |
|---|---|---|
| `src/voice-agent/prompts/anchor_rules.md` | Anchor tier (git-tracked) | New |
| `src/voice-agent/pipeline/learned_rules_v2.py` | v2 schema parser + tier-aware injection | New |
| `src/voice-agent/pipeline/prompt_builder.py` | Call v2 loader when flag enabled | Modified |
| `src/voice-agent/pipeline/turn_dispatcher.py` | Hot-reload watcher (already exists, unchanged) | Unchanged |
| `src/voice-agent/tools/log_analyzer.py` | Switch input from `conversations.db` to `turn_telemetry.db`; add telemetry-signal mining | Modified |
| `src/voice-agent/pipeline/evolution/__init__.py` | New package | New |
| `src/voice-agent/pipeline/evolution/live_capture.py` | Producer A | New |
| `src/voice-agent/pipeline/evolution/batch_miner.py` | Producer B (replaces analyzer mining logic) | New |
| `src/voice-agent/pipeline/evolution/contradiction_detector.py` | Producer C | New |
| `src/voice-agent/pipeline/evolution/reinforcement_tracker.py` | Producer D | New |
| `src/voice-agent/pipeline/evolution/evaluator.py` | 5-stage evaluator pipeline | New |
| `src/voice-agent/pipeline/evolution/lifecycle.py` | Tier transitions, rollback, quarantine | New |
| `src/voice-agent/pipeline/evolution/golden_eval.py` | Canonical-response eval runner | New |
| `src/voice-agent/pipeline/evolution/report.py` | Daily report writer | New |
| `src/voice-agent/tests/golden_evolution_canonical.jsonl` | 50-prompt golden set | New |
| `src/voice-agent/tools/evolution_voice.py` | Voice tools (evolution_status, revert_rule, etc.) | New |
| `bin/jarvis-rules` | CLI entry point | New |
| `bin/jarvis-rules-migrate-v2.py` | One-shot v1→v2 migration | New |
| `bin/jarvis-evolution-eval.sh` | Nightly golden-eval runner | New |
| `~/.jarvis/learned_rules.md` | Runtime rule store (migrated to v2 schema) | Modified format |
| `~/.jarvis/evolution_report.md` | Daily summary | New |
| `~/.jarvis/evolution_log.jsonl` | Append-only event log | New |

## 6. Data flow

```
Per-turn (in-process):
    user_text → STT → supervisor → TTS → telemetry write
                                       → live_capture.observe()
                                         (if correction phrase → proposal)
                                       → reinforcement_tracker.observe()

Background (asyncio task, every 12h):
    batch_miner.run() → telemetry signal mining → proposal list
                     → evaluator.run(proposal) for each
                     → write to learned_rules.md (auto-stage) or proposals.md (HITL)

Background (asyncio task, every 24h):
    contradiction_detector.run() → archival proposals → evaluator → archival or HITL

Daily (06:00 local, systemd timer or asyncio task):
    golden_eval.run() → score current rule set
    lifecycle.promote() → staged → accepted if eligible
    report.write() → ~/.jarvis/evolution_report.md

On rule file change (mtime watcher in turn_dispatcher, already exists):
    learned_rules_v2.load() → tier-aware injection → update_instructions()
```

## 7. Error handling

- All evolution code paths are async background tasks. Any exception is caught at the task boundary and logged; never crashes the user-facing turn.
- Evaluator stage failures are non-fatal — the proposal is dropped or routed to HITL.
- LLM-call failures (rate limit, breaker open) are retried with exponential backoff up to 3 attempts; after that the stage is skipped with `reason=infra_failure` and the proposal returns to the next mining cycle.
- File-write failures on `learned_rules.md` are caught and re-queued; the in-memory rule set continues serving from last known good.
- Anchor sha256 mismatch at boot → fail fast with a clear error to logs. Voice-agent does not start until anchor file is restored or sha baseline is updated through the proper path.

## 8. Testing strategy

**Unit:**
- Schema parser round-trip (v2 markdown → struct → v2 markdown).
- Each evaluator stage with mocked LLM responses for pass/fail cases.
- Golden eval scorer matches expected scores on a fixture.
- Migration script idempotency.

**Integration:**
- Full pipeline against the 22 historical pending proposals → expected staging decisions match a hand-rated ground truth.
- Anchor sha protection: tamper with anchor file → boot fails.
- 1-turn rollback: synthesize "stop doing X" follow-up to a staged-rule turn → rule demotes within next mining cycle.
- Hot-reload preservation: v1 loader and v2 loader produce equivalent prompts on the migrated file during the cutover window.

**Behavioral:**
- Persona protection: feed the evaluator a proposal that would change "Yes?" → Stage 2 routes to NEEDS_REVIEW.
- Over-correction: feed a proposal "never open Chrome" → red-team finds the "the user explicitly asked you to open Chrome" probe → rejected.
- Bulk-retirement guard: contradiction detector proposes 6 retirements → bulk guard blocks, routes to HITL.

**Soak:**
- Phase 5 ships first in logging-only mode for 7 days ("would have staged X"). Compare proposed actions against Ulrich's intuition before flipping to live auto-stage.

## 9. Rollout plan (7 phases, ~12 working days)

| Phase | Duration | Deliverable | Gate to next |
|---|---|---|---|
| 1 | 1 day | Fix input — analyzer reads `turn_telemetry.db` not `conversations.db`. Backfill the 22 historical proposals (or mark them as v1 legacy) | Analyzer produces ≥1 sensible new proposal from telemetry |
| 2 | 1 day | Schema migration. v2 parser + parallel loader behind flag. Anchor file extracted with hand-curated list. Migration script run. | v2 loader produces equivalent prompt to v1 on migrated file (tested in shadow) |
| 3 | 2 days | Producers A, B, C, D. Write to proposals only — no auto-staging | All four produce evidence within 24 h of deployment |
| 4 | 3 days | Evaluator pipeline. Calibrate judges against Ulrich's hand-rating of the 22 backlog (target κ ≥ 0.7 per judge). Run against batch of historical proposals. | All five stages execute end-to-end without error on a fixture set |
| 5 | 2 days | Auto-staging + 1-turn rollback + quarantine. Deploys in logging-only first ("would have staged X") for 7 days. | Logging-only run for 7 days shows reasonable staging decisions |
| 6 | 2 days | Promotion machinery (staged → accepted, HITL flow for accepted → core), reinforcement tracker counters wired | First staged rule auto-promotes after 7 days clean shadow |
| 7 | 1 day | Observability: daily report, voice tools, CLI | Daily report fires at 06:00; voice tools registered and callable |

## 10. What this spec does NOT cover (deliberately out of scope)

- **Editing `prompts/supervisor.md` automatically.** Persona-level prompt edits stay manual. Auto-evolution only changes `learned_rules.md`.
- **Auto-creating new subagents or tools.** That's Voyager-style skill acquisition — a separate spec.
- **Auto-tuning numeric heuristics** (VAD threshold, min_words, retry ceilings) from telemetry. Separate spec.
- **Multi-user / family-mode rule partitioning** (Lizzie's rules vs. Ulrich's). Not needed yet.
- **Online prompt mutation during a turn.** Voice budget forbids it; all work is offline.
- **A/B cohort splits at the SFU level.** Single-user system; shadow mode + replay-delta suffice.

## 11. Open questions for the implementation plan

- **Judge calibration data source.** The 22 backlog gives 22 hand-rated examples — possibly enough for κ ≥ 0.7 but tight. May need to bootstrap with synthetic disagreement cases during Phase 4.
- **GPT-5 access cost vs. GPT-5-mini.** $20 OpenAI budget loaded; mini may suffice for judge role given the unanimous-of-3 redundancy.
- **Contradiction detector cadence.** 24 h is the design default; might tighten to 12 h if dead-ref proposals pile up faster than retirement throughput.
- **Reinforcement-turn definition.** Current heuristic is "rule was in effect AND no negative correction within 30s" — may need to also exclude turns where the rule was simply unobservable (rule about Chrome but the turn was about weather).

## 12. References

- [Voyager (Wang et al. 2023)](https://arxiv.org/abs/2305.16291) — skill-library pattern
- [Reflexion (Shinn et al. 2023)](https://arxiv.org/abs/2303.11366) — episodic-buffer reflection
- [PromptBreeder (Fernando et al. 2023)](https://arxiv.org/abs/2309.16797) — self-referential prompt evolution
- [AlphaEvolve (DeepMind 2025)](https://arxiv.org/abs/2506.13131) — production evolutionary loop
- [Evaluator Stress Tests (2025)](https://arxiv.org/abs/2507.05619) — reward-hacking detection
- [PoLL — Replacing Judges with Juries (2024)](https://arxiv.org/abs/2404.18796) — ensemble of small judges
- [Self-Preference Bias in LLM-as-a-Judge (2024)](https://arxiv.org/html/2410.21819v2) — proposer-judge separation requirement
- [Persona Drift in LM Dialogs (2024)](https://arxiv.org/html/2402.10962v1) — attention decay to system tokens
- [Anthropic Managed Agents — Versioning & Rollback](https://platform.claude.com/cookbook/managed-agents-cma-prompt-versioning-and-rollback) — immutable version pinning
- [Anthropic Skills for Enterprise](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/enterprise) — 3-5 prompt eval suite requirement
- [Cursor Rules](https://cursor.com/docs/context/rules) — `.mdc` frontmatter precedent
- [Cline Rules + Prompt Learning](https://docs.cline.bot/features/cline-rules) — concatenation pattern
- [Letta Memory Blocks](https://docs.letta.com/advanced/memory-management/) — tier model
- [Cognee memify](https://docs.cognee.ai/core-concepts/main-operations/memify) — usage-reweight retirement
- [Shadow Mode Rollouts (Brightlume 2025)](https://brightlume.ai/blog/shadow-mode-rollouts-ai-agents-pilot-production) — production shadow practice
