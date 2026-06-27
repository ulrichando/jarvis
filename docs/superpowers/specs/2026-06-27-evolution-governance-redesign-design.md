# Evolution governance redesign — design

**Status:** approved (brainstorm 2026-06-27), ready for implementation plan
**Scope:** sub-project A of Ulrich's 2026-06-27 evolution request. Sub-project B
(notification access via D-Bus, no vision) is **separate** and gets its own
spec/plan.

## Problem

The self-evolution loop's only spend control is a per-day **build *count*** cap
(`pipeline/automod/throttle.py`, default 5, `JARVIS_AUTOMOD_DAILY_CAP`). Three
failures:

1. **A count can't measure the real cost.** One build can cost 10× another. The
   actual fear — "run out of token" — is dollars, not builds. (Builds record no
   cost today: `automod-*.json` has no token/cost field.)
2. **It doesn't even hold.** Live `throttle.json` showed `admitted_today: 18` on
   2026-06-26 before the user hand-paused the loop (`.evolution-paused`).
3. **It runs on a clock, not a reason.** `evolution-nightly.timer` fires **every
   30 min** and tends to *always build something*, regardless of whether there's
   a real improvement to make or whether the user is mid-conversation.

Plus a dashboard bug: the `/evolution` nav badge / queue display reads
`data.proposals.length` (built proposals awaiting review) and presents it as the
**queue depth**, so the "number in queue" is wrong — it never reflects the 17
queued intents in `queue.jsonl`.

## Goals

- Replace the build-count cap with a **cost budget** as the hard brake.
- Build only with a **reason** (a queued improvement intent, recurring error,
  failing test, or explicit user ask) **and spare capacity** (idle + budget +
  cooldown + nothing in-flight).
- Keep evolution **autonomous and proactive** on a deliberate cadence: a gated
  build tick **every 4 hours** plus the **nightly** deep passes.
- Fix the dashboard to show the **actual queue depth**.

## Non-goals

- Auto-deploy. **A human approves every deploy** (unchanged).
- Touching the path-blocklist, watchdog auto-rollback, or per-topic in-flight
  lock — all preserved.
- Notification access (sub-project B).
- Editing `automod/` via auto-mod itself — `pipeline/automod/**` is on the
  HARD_BLOCKLIST; every change here is **human-edited**.

## Design

### 1. The governance gate

`throttle.py::admit_intent(intent)` is redesigned. It returns `(admit, reason)`
and admits a queued intent only if **all** pass:

```
admit_intent(intent):
  if not intent.text:            return False, "empty_intent"
  if any blocked path in hint:   return False, "blocked_path:…"        # unchanged
  if not _is_idle():             return False, "not_idle"              # NEW
  if cost_ledger.spent_today() >= daily_usd():  return False, "budget_exhausted"   # NEW (replaces count cap)
  if _since_last_build() < cooldown_min():      return False, "cooldown"            # NEW
  return True, ""
  # per-topic in-flight cap (1) stays enforced by the spawner lockfile
```

- **Signal** is implicit and already produced: an intent only reaches the gate
  because the existing detectors (`patterns.py`, error log, introspection,
  explicit user request) put it in `queue.jsonl`. Empty/stale queue → nothing to
  admit → no build. So the gate needs no separate "signal" check; the *presence
  of a worthwhile queued intent* is the signal. (Stale/circuit-broken intents are
  already filtered by the existing dedup + `circuit_breaker.json`.)
- **`_is_idle()`** — no voice turn in the last `JARVIS_EVOLUTION_IDLE_MIN` (10)
  minutes, via the `turn_telemetry.db` recency query already used by
  `jarvis-restart-all`:
  `SELECT (julianday('now')-julianday(MAX(ts_utc)))*86400 FROM turns`.
- **Cooldown** — `JARVIS_EVOLUTION_COOLDOWN_MIN` (default 60): min minutes since
  the last admitted build, tracked in `throttle.json` (`last_build_ts`).
- The `admitted_today` counter and `daily_cap()` are **removed** from the gate
  (kept only as an optional emergency backstop env `JARVIS_AUTOMOD_DAILY_CAP`,
  default unset/∞).

### 2. Cost ledger (new — the real brake)

New module `pipeline/automod/cost_ledger.py`:

- `record(build_id, cost_usd)` — append `{date, build_id, cost_usd}` to
  `~/.jarvis/auto-mods/cost-ledger.json` (lock-protected atomic write, same
  pattern as `throttle.py`).
- `spent_today()` — sum `cost_usd` for the current UTC date (date rollover resets
  to 0, like `throttle.py`).
- `daily_usd()` — `float(os.environ.get("JARVIS_EVOLUTION_DAILY_USD", "6"))`.

**Cost capture** (primary): the build wrapper `bin/jarvis-automod-impl` runs the
Claude-Code-shaped build agent; invoke it so its final result yields
`total_cost_usd` (Claude-Code reports this in `--output-format json`), and
`finalize.py` calls `cost_ledger.record(id, total_cost_usd)` when the build
completes (pass or fail — a failed build still spent tokens). **Fallback** if the
agent's cost isn't cleanly reportable: sum the `:4000` proxy's per-request usage
(`~/.jarvis/proxy.log`) over the build's start→end window, keyed by a
`X-Jarvis-Build-Id` header injected for the build subprocess. **Backstop:** if
neither yields a number, fall back to the conservative count cap so the budget
can never silently become infinite.

### 3. Cadence

- `setup/systemd/jarvis-evolution-nightly.timer` — change from
  `OnUnitActiveSec=30min` to **`OnCalendar=*-*-* 00/04/08/12/16/20:00:00`**
  (every 4 hours). Each fire runs the gated drain: at most one build, only if the
  gate passes. ~most ticks no-op (cheap, no LLM beyond the gate checks).
- **Nightly deep passes unchanged:** `jarvis-evolution-introspect.timer` (05:30,
  generates the next batch of intents) and `jarvis-evolution-soak.timer` (02:30,
  calibration).
- `jarvis-evolution-watchdog.timer` (every 20s, rollback) — **unchanged**.

Budget × cadence interaction (documented, not a bug): 6 build *opportunities*/day,
but `daily_usd` caps actual spend — at ~$1/build and $6/day, ~all 6 can run; at a
tighter budget, later slots no-op on `budget_exhausted`.

### 4. Dashboard queue-count fix

- `src/web/src/hooks/use-evolution-count.ts` — the nav badge should reflect
  **queue depth** (intents waiting to build), not `proposals` (built, awaiting
  review). Read the queued count from the API.
- `src/web/src/app/api/evolution/route.ts` — ensure the GET response exposes an
  explicit `queueDepth` (= `queued.length` + in-flight), distinct from
  `proposals`. The `/evolution` page table labels each correctly (Queued vs
  In-flight vs Proposals/Awaiting-review vs Deployed) so the counts stop
  conflating.

## Components & data flow

```
detectors (patterns.py / error log / introspection / user ask)
   → queue.jsonl  (worthwhile intents = the "signal")
4-hourly tick (jarvis-evolution-nightly → drain_queue)
   → throttle.admit_intent(intent):  idle? budget? cooldown? blocklist?
        ├─ no  → skip (reason logged), tick ends doing nothing
        └─ yes → spawner spawns bin/jarvis-automod-impl (worktree, in-flight lock)
                    → build → test/review gates → finalize.py
                          → cost_ledger.record(id, total_cost_usd)
                          → proposal (human approves) → deploy → watchdog (rollback safety)
```

State files (`~/.jarvis/auto-mods/`): `queue.jsonl`, `throttle.json`
(`last_build_ts`, optional backstop counter), **new** `cost-ledger.json`,
`circuit_breaker.json`, `.evolution-paused`.

## Config (env)

| Var | Default | Meaning |
|---|---|---|
| `JARVIS_EVOLUTION_DAILY_USD` | `6` | hard daily cost ceiling (the brake) |
| `JARVIS_EVOLUTION_IDLE_MIN` | `10` | min idle (no voice turn) before a build |
| `JARVIS_EVOLUTION_COOLDOWN_MIN` | `60` | min minutes between builds |
| `JARVIS_AUTOMOD_DAILY_CAP` | unset (∞) | emergency count backstop only |

## Error handling / safety (unchanged)

3-layer path-blocklist, watchdog auto-rollback (live-proven), human-approves-every-
deploy, per-topic in-flight lock, `circuit_breaker.json` for repeatedly-failing
intents. `.evolution-paused` still hard-stops the loop.

## Testing

- Unit (`tests/`, human-written, run with the voice-agent venv):
  - `cost_ledger`: record/sum/rollover/atomic-write; `spent_today` after rollover = 0.
  - `admit_intent`: each gate in isolation — not-idle blocks; budget-exhausted blocks;
    cooldown blocks; blocked-path blocks; all-pass admits. Idle + budget mocked.
  - cadence: gate skip leaves no worktree / no spend.
- Integration: a dry-run drain with a stub intent confirms a build is admitted only
  when idle+budget+cooldown pass, and `cost-ledger.json` is appended on finalize.
- Full suite green before any restart (regression-prevention rule 5).

## Rollout

Phased (writing-plans): (1) `cost_ledger` + capture wiring, (2) gate redesign
(idle/budget/cooldown, remove count cap), (3) cadence timer cutover, (4) dashboard
fix, (5) soak in shadow then unpause. Each phase committed + tested; the loop stays
paused until the gate + ledger are proven, then `.evolution-paused` is cleared.
