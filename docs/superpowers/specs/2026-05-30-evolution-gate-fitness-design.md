# JARVIS Self-Evolution — The Gate (Honest Fitness Function)

**Date:** 2026-05-30
**Status:** proposed (design) — awaiting review
**Scope:** new **read-only** subsystem `src/voice-agent/evolution/` + a CLI `bin/jarvis-evolution`
+ an append-only ledger store. **No behavior change. No candidate generation, evaluation,
or application yet.**

## Why this first

We established (research-grounded, 2026-05-30) that self-evolution which genuinely raises
*capability* — **Tier 2**: the system authoring and validating its own skills/tools/code —
is doable on JARVIS, but only if **selection is external and honest**. Real evolution's
defining property is that the *environment* judges fitness, not the organism. An LLM that
grades its own fitness reward-hacks within days ("self-rewarding → reward hacking" is an open
problem in the literature; it is the **JARVIS-cancer** failure mode — optimizing a measurable
proxy against the user's real interest).

The user chose **"autonomous within the gate."** Autonomy makes the gate the *only* thing
between a proposed self-modification and a live change on a `NOPASSWD: ALL` machine. Therefore
the gate — a trustworthy, external, hard-to-game **fitness function** plus the **ledger** that
gives selection heredity — must exist, be calibrated, and be *trusted* before any autonomous
proposal/application loop is built. This spec is that foundation and nothing more.

## Goal

A read-only subsystem that, from JARVIS's real interaction record, produces a **trustworthy
fitness reading** — a vector of externally-grounded sub-scores + guardrails + a transparent
composite — and records readings to an append-only **evolution ledger**. It must be
calibratable against felt experience (back-tested on known-good / known-bad windows) and
**impossible to satisfy by self-report**.

**Explicit non-goals (this increment):** no candidate generation, no A/B, no application of
any change, no LLM self-grading, no behavior change to JARVIS. Pure measurement + record.

## Design

### Principle 1 — fitness is external, and a vector, never a self-graded scalar
Every signal is derived from **environment behavior** (what the user did) or a **verifiable
outcome** (did the action actually succeed), never from the assistant's own assessment. The
reading is a **vector with guardrails**, not one scalar — because any single proxy is gameable
(minimize "interrupts" → JARVIS talks less → worse; minimize "turns" → JARVIS dodges work). A
change is only "fitter" if it improves the composite **without regressing any guardrail axis
past its floor** (lexicographic veto).

### Principle 2 — the signals (all from data that already exists)
From `~/.local/share/jarvis/turn_telemetry.db` (read-only) + the conversation record. Each
axis: source, direction, gaming-risk → mitigation.

| Axis | Signal (source) | Better = | Gaming risk → mitigation |
|---|---|---|---|
| **Task success** | tool ran AND `had_tool_error`=0; structured tool-result evidence present (`confab_check_state`) | higher | "claim success without acting" → require *verifiable* tool_result (confab detector already enforces this) |
| **Correction rate** | `correction_signal` + re-ask detection (same intent repeated within N turns) | lower | "avoid corrections by doing nothing" → vetoed by the task-success guardrail |
| **Interruption rate** | `interrupted` / barge-in telemetry | lower | "talk less to avoid barge-in" → vetoed by task-success + completeness guardrails |
| **Confab rate** | confab-detector drops + denial-detector blanks | lower | self-evident; sourced from the detectors, not self-report |
| **Latency** | `ttfw_ms` (time-to-first-word) | lower | "be fast by being wrong" → vetoed by correction + success guardrails |
| **Recurring failure** | growth/decay of `recurring_errors` / `tool_gap_patterns` rows | fewer / decaying | hard to game (external pattern tables) |

(Final axis set + weights are an open decision — see below.)

### Principle 3 — reading shape
- Per-axis normalized sub-score over a window of real turns.
- **Guardrails:** each axis has a floor; a later candidate that pushes any axis below its floor
  is disqualified regardless of composite.
- **Composite:** a transparent, inspectable weighting whose weights live in a **committed,
  human-owned config** (part of the constitution). The composite is for *ranking*; the
  guardrails are for *vetoing*.
- **Counterfactual-ready:** computed over an attributable window so that later, the *delta*
  between a candidate-variant window and the incumbent window is the candidate's fitness —
  never an absolute self-score.

### Principle 4 — heredity: the evolution ledger
Append-only store (new `~/.local/share/jarvis/evolution_ledger.db`, **separate** from telemetry
so we never mutate the telemetry schema). Records per reading: timestamp, window bounds,
per-axis scores, composite, guardrail flags, and (later) the candidate id it is attributed to.
This is selection's memory — it reveals drift, and later prevents re-trying culled variants
(dedup-vs-seen).

### Principle 5 — trust-building (who validates the validator)
The hardest part of an honest fitness function is *trusting* it. So:
1. **Read-only soak.** Env-gated (`JARVIS_EVOLUTION_GATE`, default OFF). It only reads + logs.
   Nothing in JARVIS depends on it.
2. **Back-test.** A harness scores known windows: the 2026-05-30 stuck-indicator + wedged-turn
   period **must** score worse; smooth, low-correction sessions **must** score better. If the
   fitness function disagrees with reality, it is wrong, and we fix it before trusting it.
3. **Calibration period.** `bin/jarvis-evolution score [--since ...]` shows the reading; the
   user sanity-checks it against felt experience over time. Only after it tracks reality do
   later increments depend on it.

### Where it lives + what it may touch
- New package `src/voice-agent/evolution/`:
  - `signals.py` — extract external signals from telemetry rows (**pure functions**).
  - `fitness.py` — vector + guardrails + composite (**pure functions**).
  - `ledger.py` — append-only writer/reader for `evolution_ledger.db`.
  - `backtest.py` — the validation harness.
- `bin/jarvis-evolution` — CLI to compute / display / back-test.
- **Reads** `turn_telemetry.db` read-only; **writes** only its own `evolution_ledger.db`.
  Touches nothing in JARVIS's live path. No import-time side effects in the voice-agent.
- **Constitutional note:** once trusted, `src/voice-agent/evolution/fitness.py` + the weights
  config go onto the auto-mod `HARD_BLOCKLIST_PATHS`. The evolver must **never** edit its own
  fitness function — that is the "fitness landscape is fixed from the evolver's point of view"
  invariant; violating it is the cancer path. (Amended in a later increment, not this one.)

## Testability
- `signals.py` + `fitness.py` are pure functions over telemetry rows → unit-testable on
  synthetic fixtures: a good window, a *gamed* window (fast-but-wrong, or quiet-but-unhelpful),
  a degraded window.
- The guardrail veto is tested explicitly: a fast-but-wrong window must **not** score fitter
  than a slower-but-correct one.
- `backtest.py` is validated against real labeled windows pulled from the live DB.

## Interactions / non-goals
- **Complements**, does not replace, the existing telemetry + the 10-axis voice-intelligence
  rubric. The rubric is a periodic human/LLM judgement of *quality*; this is a continuous,
  behavior-grounded *fitness* signal for *selection*. They can cross-check each other.
- **Does not** change routing, prompts, memory, or any runtime behavior.
- **Does not** evaluate or apply candidate changes — that is the *next* spec, and it may only
  be built once this gate is calibrated and trusted.

## Risks
- **Proxy gaming** — mitigated by the vector + lexicographic guardrails: no single axis can be
  optimized in isolation.
- **Sparse signal** — honest signals (corrections) are rare; fitness accumulates over many
  turns. Sparse = slow selection, accepted by design.
- **Mis-calibration** — mitigated by the back-test + read-only soak + human calibration before
  anything depends on it.
- **The meta-problem (validating the validator)** — there is no fully self-validating fitness
  function. We substitute back-testing against reality + human calibration, exactly as the
  Darwin–Gödel Machine substitutes empirical validation for the original Gödel machine's
  impossible "provably beneficial" requirement.

## Open decisions (for your review)
1. **Axis set + weights.** The 6 axes above and their relative weights. Recommendation:
   task-success + correction-rate as hard guardrails; latency carries the lowest weight.
2. **Scope of measurement.** JARVIS-Claude turns only (richest telemetry) or also the direct
   modes (gemini/openai)? Recommendation: Claude-only first.
3. **Ledger location.** Separate `evolution_ledger.db` (recommended) vs. a new table inside
   `turn_telemetry.db`.
