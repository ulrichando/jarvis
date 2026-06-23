# Cognitive evolution loop — design

**Date:** 2026-06-23
**Status:** proposed — safety guard (Phase 0) landed; spec awaiting user review before Phase 1+.
**Related:** [2026-05-24 source self-mod](2026-05-24-jarvis-source-code-self-mod-design.md) · [2026-06-23 feedback intelligence](2026-06-23-evolution-feedback-intelligence-design.md) · [2026-06-23 coverage gate](2026-06-23-evolution-mutation-test-gate-design.md)

## Vision

Evolution should behave less like a cron job and more like a mind: **experience →
a thought → reasoning → an intention → an act → learning from the result (especially
mistakes).** JARVIS already has most of these faculties; today they fire on a 30-minute
clock instead of from lived experience, and the in-process loop builds even in manual
mode. This spec rewires them into an experience-driven loop with durable memory of
mistakes.

## Faculties → mechanisms (mostly already exist)

| A mind… | Mechanism | Status |
|---|---|---|
| **Experiences** | turns/tool-outcomes/corrections/errors → `turn_telemetry.db`, confab DB, `conversations.db`, file memory | exists |
| **Has a thought** when something matters | a turn surfaces a *mistake / correction / new fact / fitness drop* → fires a signal | **clock today → make event-driven (Phase 1)** |
| **Reflects & reasons** | `introspection.run_self_assessment` (LLM over its own flaws + telemetry) | exists → **ground in the triggering experience (Phase 2)** |
| **Forms an intention** | `patterns.scan_and_emit` / `introspection.enqueue_improvements` → a testable change | exists |
| **Acts** | build → tests → coverage gate → *human review* (manual) / deploy (auto) → watchdog | exists |
| **Learns from mistakes** | `patterns.build_retry_intent` (failure → "try a different approach"); watchdog rollback | partial → **durable cross-session lessons memory (Phase 3)** |

## Phase 0 — safety guard (DONE 2026-06-23)

`jarvis_agent.py::_automod_loop` called `drain_queue()` every 30 min with **no
auto-mode check** → it autonomously built in manual mode (the root of "how is he
building in manual mode"). Fixed: the loop now `scan_and_emit`s always (queueing is
fine in manual) but only `drain_queue`s (builds) when `is_auto_mode()`. The nightly
path already gated this way; the two now agree. **Manual mode = queue for review,
never autonomous build.**

## Phase 1 — thoughts fire from experience, not a clock

- A process-level `asyncio.Event` ("experience signal") in the agent.
- The turn-end hook (`on_user_turn_completed` / `conversation_item_added`) does a
  **cheap** check on the just-finished turn — did it record any of: a tool/agent
  **error**, a **correction**, a **confab** flag, or a **`memory`-tool write** (a new
  fact)? If so, set the event. (The hook only decides "something happened, go look";
  the existing threshold logic stays in `scan_and_emit`.)
- `_automod_loop` waits on the event with a **slow backstop timeout** (`~2 h`, env
  `JARVIS_AUTOMOD_BACKSTOP_S`) instead of a fixed 30-min sleep. On wake: clear, debounce
  (short cooldown after a build to avoid thrash), `scan_and_emit`, then mode-gate the
  build exactly as Phase 0.
- Net: evolution reacts to what just happened; the backstop guarantees nothing is
  missed; manual mode still only queues.

## Phase 2 — reflection grounded in the experience

`run_self_assessment` currently reasons from aggregate telemetry. Pass the **specific
triggering signals** (the recent error/correction/fact that woke the loop) into its
evidence, so the "thought" is *about what just happened* ("I misheard X" / "I was wrong
about Y → here's the change") rather than a generic scan. Reuse `gather_evidence`; add a
`recent_signals` field.

## Phase 3 — a durable lessons-learned memory

Today a failed build's lesson lives only in that lineage's `prior_failures` (in-artifact,
single attempt chain). Add a **durable, cross-session lessons thread** — append
`{intent, approach, failure_reason, lesson, ts}` on every failed/rolled-back build to a
store (preferred: the self-hosted **honcho** memory backend, already wired; fallback:
`~/.jarvis/auto-mods/lessons.jsonl`). The reflection (Phase 2) reads it so JARVIS doesn't
re-propose an approach it already learned fails — evolving *forward* instead of in circles.

## Mode policy (unchanged, enforced everywhere)

- **Manual** (default): detect + reflect + **queue** for your review. Never auto-builds.
- **Auto**: detect + reflect + **build** (debounced, capped at the daily budget), human
  review still required to deploy unless graduated. `AUTO_MERGE` stays off.

## Non-goals (keep it grounded — not an AGI rewrite)

- No new "agent brain" module, no continuous background LLM "thinking" burning tokens.
  Reflection runs **on a triggering event**, capped + debounced.
- No change to the build/test/deploy/watchdog actuators — they work.
- No removal of the human review gate.

## Architecture / files

- `jarvis_agent.py` — turn-end hook sets the experience signal; `_automod_loop` waits on
  it (Phase 1).
- `pipeline/automod/introspection.py` — `gather_evidence` gains `recent_signals`; reflection
  reads the lessons store (Phase 2/3).
- `pipeline/automod/lessons.py` *(new)* — append/read the durable lessons thread; honcho-backed
  with a jsonl fallback (Phase 3).
- `pipeline/automod/patterns.py` — unchanged detectors; `build_retry_intent` also writes a
  lesson.

## Verification

- Phase 0: `test_automod_spawner` + a new test asserting the loop does **not** drain in
  manual mode and **does** in auto mode.
- Phase 1: unit-test the turn-end signal classifier (error/correction/confab/fact → event)
  + the backstop wait; assert manual mode still queue-only.
- Phase 3: unit-test lessons append/read + that a known-failed approach is excluded from new
  intents.
- Full `pytest tests/` green at each phase.

## Scope

```
SCOPE:  jarvis_agent.py (turn-end signal + event-driven loop)
        pipeline/automod/{introspection.py, lessons.py(new), patterns.py}
        tests/ (per-phase)
OUT:    the build/test/finalize/deploy/watchdog actuators, _state HARD_BLOCKLIST,
        the coverage gate, the web UI (separate), src/cli.
WHY OUT: this is about WHEN/WHY JARVIS evolves (triggering + learning), not HOW it
         builds — that pipeline already works.
```
