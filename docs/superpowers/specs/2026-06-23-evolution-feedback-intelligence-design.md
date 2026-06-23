# Evolution — Feedback & root-cause intelligence (sub-project A)

Date: 2026-06-23

First of a four-part program to make JARVIS's self-evolution loop trustworthy
enough to eventually graduate toward autonomy. Sequence: **A (this) → B
dashboard depth → C notifications → D autonomy graduation.** A is the evidence
layer; D depends on A+B existing.

## Problem

The soak gate already scores five fitness axes per ledger reading
(`per_axis_json`: `reask, confab, latency, action, interruption`), but only the
`composite` is ever read. Live data shows `latency 0.40` while every other axis
is ≥0.77 — a clear, actionable weak spot that nothing surfaces or acts on. The
loop proposes from corrections/confab/errors, never from *where fitness is
weakest*. Separately, `correction_signal` grouping is exact-match, so related
corrections fragment (and live signals like `"don't want"` look low-quality).

## Goal

Close the feedback loop on per-axis fitness: detect the persistently-weak axis
and emit a concrete, file-pointed proposal targeting it. Plus a light scaffold
(a recorded `root_cause` label) so correction bucketing can grow later.

## Design

Additive to the existing detector (`_scan_corrections / _scan_confabs /
_scan_errors`); no rewrites.

### 1. `pipeline/automod/fitness_feedback.py` (new, single purpose)
- Reads the last `LOOKBACK_M = 5` readings via `evolution.ledger.read_readings()`
  (returns parsed `per_axis` dicts, newest-first).
- `weak_axis(readings) -> (axis, evidence) | None`: among axes present, pick one
  that is (a) below `FITNESS_FLOOR = 0.6` in the latest reading, (b) below the
  floor in ≥ `PERSIST_N = 3` of the last M (persistence guard against one-off
  dips), (c) the lowest latest value among those. `evidence` = `{axis, latest,
  n_below, window_m, floor}`.
- `AXIS_INTENTS`: each axis → a concrete, file-pointed intent + rationale
  template, so the spawner subagent gets something buildable (not "make latency
  better"). e.g. latency → "profile the slow turn path (pipeline/turn_router,
  providers/llm cache, providers/tts) and cut TTFW"; reask → clarify/routing;
  confab → supervisor / tool-description prompt strength; action → tool routing;
  interruption → barge-in tuning. `build_intent(axis, evidence) -> dict | None`
  (None if axis unmapped).
- Pure-ish: only reads the ledger. Constants are module-level (tunable; start
  global floor, add per-axis floors only if it misfires).

### 2. `_scan_fitness()` in `patterns.py`
- Gets `weak_axis()`; on a hit, dedups via the existing `recurring_corrections`
  table with synthetic signal `__fitness_axis_<name>__` (exactly how
  `_scan_confabs` dedups), then `_emit()`s the built intent with `kind="fitness"`
  and the per-axis `evidence`. Wired into `scan_and_emit()` as the 4th scanner.
- Dedup-once semantics (mirrors confab): a weak axis proposes once until built;
  re-proposal after a cooldown is a future iteration (noted, not built).

### 3. `criteria.py`
- Add `"fitness": ("self_optimization", "Self-optimization")` to `_KIND_GOAL`
  so fitness intents get a precise goal label. `enrich_record` already sets
  `source="autonomous"` for non-explicit kinds — correct for fitness.

### 4. Correction root-cause scaffold (light, non-breaking)
- Add `_normalize_signal(s)` (lowercase / trim / strip punctuation + a tiny
  synonym map) and record the normalized form as a `root_cause` field on emitted
  correction intents. The SQL grouping/dedup key is left unchanged (no
  behavior change for the existing tested path). Full merge-grouping +
  embedding/LLM clustering is **deferred** until correction volume justifies it.

### 5. API (`web/.../api/evolution/route.ts`)
- Extend the `fitness` payload with `perAxis` (parsed from the latest
  `per_axis_json`) and `weakAxis` (recomputed cheaply, or the lowest perAxis
  entry) so dashboard **B** can render the breakdown. Small read; no new dep.

## Data flow
soak → ledger `per_axis_json` (exists) → `fitness_feedback` reads last M → weak
axis → `_scan_fitness` emits a targeted intent → queue → existing Build it /
review path.

## Safety
Reuses the daily cap + dedup table (can't spam); persistence guard avoids
one-off reactions; only mapped axes emit; floor + lookback are tunable.

## Testing
Hermetic (temp ledger + `JARVIS_HOME`): `weak_axis` picks the right axis + honors
the persistence guard + returns None when all axes healthy; `_scan_fitness`
emits once then dedups on re-run; `build_intent` renders concrete file-pointed
text; `_normalize_signal` collapses `"too wordy" / "be shorter"`-style variants.

## Out of scope (lands elsewhere)
Per-axis *visualization* (→ B); semantic embedding/LLM correction clustering
(deferred); passive biasing of unrelated intents (YAGNI); fixing the upstream
correction extractor.
