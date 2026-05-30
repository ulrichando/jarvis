# JARVIS Self-Evolution — Calibration Soak (Trust-Building Increment)

**Date:** 2026-05-30
**Status:** proposed (design) — awaiting review
**Scope:** add a daily, automated, read-only soak that accumulates fitness readings into the
existing `evolution_ledger.db`, plus a `trend` review surface — so the gate can be validated
against felt experience before anything depends on it. **No runtime behavior change. The gate
stays globally OFF — only the soak unit flips it.**
**Builds on:** `2026-05-30-evolution-gate-fitness-design.md` (the read-only honest fitness gate,
already shipped + calibrated: 26/26 tests, back-test good `0.8523` > bad `0.6676`).

## Why this, why now

The gate's foundation is shipped and passes its back-test calibration anchor. But the spec that
built it is emphatic (Principle 5, "who validates the validator"): the gate must be **calibrated
and *trusted*** — sanity-checked against felt experience over time — before any autonomous
proposal/application loop is built. The ledger is currently **empty**: nothing has logged a
reading yet, so there is no longitudinal record to build that trust on.

This increment is exactly that trust-building step and nothing more: log one honest reading per
day, automatically, and give the user a scannable surface to answer *"did yesterday feel as rough
as the gate says — and which axis drove it?"* It remains pure measurement + record. No candidate
generation, no application, no blocklist pin (those are later increments).

## Goal

A daily systemd-timer-driven soak that writes one fitness reading per day into the append-only
`evolution_ledger.db`, over a window that matches how the gate was calibrated, plus a `trend` CLI
view of the accumulated readings. It must be idempotent, leave the gate globally OFF, touch no
runtime code, and be impossible to confuse with a self-grading loop (it only reads telemetry and
writes its own ledger).

**Explicit non-goals (this increment):** no candidate generation/evaluation/application; no LLM
self-grading; no `pipeline/automod/` or `HARD_BLOCKLIST_PATHS` change (the `fitness.py` pin is a
*separate later* increment); no telemetry-schema change; no gap-backfill (missing days stay
visible in the trend).

## Design

### Decision 1 — window: previous **local** calendar day

Each soak run scores the *previous local-time calendar day*. Rationale:

- **Matches the calibration anchor.** The back-test's GOOD window was a full day (~177 turns);
  a daily window reproduces stable per-reading `n` and avoids the full-history pathology we
  measured (p90 TTFW over all history collapses the latency axis to `0.0`; a single day yields a
  real `0.37`).
- **Local, not UTC**, because felt experience is local. The user is in EDT; "yesterday felt
  rough" means the local day. Telemetry stores `ts_utc`, so the window function computes
  yesterday in local time and converts the bounds to UTC `…Z` strings for the query.

**Verified 2026-05-30:** telemetry `ts_utc` is exactly `YYYY-MM-DDTHH:MM:SSZ` (e.g.
`2026-05-30T16:17:10Z`); lexicographic `>=`/`<=` bounds work (a yesterday-UTC bound query
returned 132 rows). The conversion is `2026-05-29 00:00 EDT → 2026-05-29T04:00:00Z` and
`23:59:59 EDT → 2026-05-30T03:59:59Z`; DST is handled by `datetime.astimezone()`. The inclusive
`…23:59:59Z` upper bound matches the back-test's exact convention; the sub-second gap to
next-midnight is negligible.

### Decision 2 — logic lives in a `soak` CLI subcommand (not a shell wrapper)

The window/dedup logic is testable Python in `evolution/soak.py` + a `jarvis-evolution soak`
subcommand, **not** shell `date` math. The timer's `ExecStart` is just `bin/jarvis-evolution
soak`. This keeps timezone arithmetic unit-testable, puts dedup next to the ledger, and matches
the existing subcommand structure (`score`/`backtest`/`ledger`).

### Decision 3 — idempotency via a read-only dedup helper (append-only preserved)

`Persistent=true` (catch-up after the laptop was off) and any double-fire could append duplicate
rows for the same day. A new **read-only** `ledger.reading_exists(window_start, window_end)` lets
`soak` skip a window it already logged. This adds **no mutation API** — the ledger stays
append-only (the test asserting `not hasattr(ledger, "update_reading"/"delete_reading")` still
holds). Gaps (days the machine was off) are **not** backfilled — they show as missing dates in
the trend, which is itself informative.

### Decision 4 — the gate stays globally OFF; only the soak unit flips it

The soak's write path is gated by `JARVIS_EVOLUTION_GATE` exactly like `score --log`
(`bool(os.environ.get("JARVIS_EVOLUTION_GATE"))` AND the log intent). The env is set **only** in
`jarvis-evolution-soak.service` (`Environment=JARVIS_EVOLUTION_GATE=1`). Consequences:
- The timer writes daily readings.
- A human running `bin/jarvis-evolution soak` by hand (no env) **computes + prints but writes
  nothing** — safe, and consistent with `score --log`'s existing behavior.
- The voice-agent runtime and every other process keep the gate OFF. Nothing imports the
  `evolution` package.

`JARVIS_TTFW_TARGET_MS=1000` is also pinned in the unit so soak readings are reproducible against
a fixed latency target regardless of any future default drift.

## Components

| Unit | What it does | How it's used | Depends on |
|---|---|---|---|
| `evolution/soak.py` | `previous_local_day_window_utc(now) -> (since_z, until_z)` — pure, `now` injected | called by the CLI `soak` command | stdlib `datetime` |
| `evolution/ledger.py` (extend) | `reading_exists(*, window_start, window_end, db_path) -> bool` — read-only `SELECT 1` | dedup guard in `soak` | sqlite (ro) |
| `bin/jarvis-evolution` (extend) | `soak` subcommand: window → dedup → `score_window` → `append_reading` (gated); `trend [--limit N]` subcommand: aligned per-axis table from the ledger | timer ExecStart (`soak`); human review (`trend`) | `soak`, `ledger`, `backtest` |
| `setup/systemd/jarvis-evolution-soak.timer` | daily `OnCalendar=*-*-* 02:30:00`, `Persistent=true`, `RandomizedDelaySec=600s` | enabled via `systemctl --user enable --now` | the service |
| `setup/systemd/jarvis-evolution-soak.service` | `Type=oneshot`, log-rotate-class hardening, `Environment=JARVIS_EVOLUTION_GATE=1` + `JARVIS_TTFW_TARGET_MS=1000`, `ExecStart=…/bin/jarvis-evolution soak` | the timer | the CLI |
| `install.sh` (extend) | register the two unit names in the timer-install + enable loops | install path | — |

### `trend` output shape

```
DATE        n    composite  reask  confab  lat    action  intr   PASS
2026-05-29  177  0.8523     0.949  1.000   0.374  1.000   0.904  ok
2026-05-28  ...
```
Pure read over `read_readings()`; `--limit` defaults to 30. The five axis columns are the
`fitness.WEIGHTS` keys (`reask`, `confab`, `latency`, `action`, `interruption`).

### systemd hardening (mirrors `jarvis-curator.service`, verified-working analog)

`jarvis-curator.service` is the proven analog — a python-under-`$HOME` `ExecStart`, `Type=oneshot`,
`ProtectHome=read-only`, `ReadWritePaths=%h/.jarvis/…`, writing to home (live status:
`Result=success`, `ExecMainStatus=0`, `NRestarts=0`). The soak mirrors it with:
- `ReadWritePaths=%h/.local/share/jarvis` (ledger write target; telemetry is opened `mode=ro` at
  the app layer so the read is read-only regardless).
- `ExecStartPre=/bin/mkdir -p %h/.local/share/jarvis` (defense against `226/NAMESPACE` if the dir
  is missing).
- `RestrictAddressFamilies=AF_UNIX` — **tighter than curator** (no network; pure local sqlite).
- The rest identical: `ProtectSystem=strict`, `NoNewPrivileges`, `PrivateTmp`, `Protect*`,
  `LockPersonality`, `RestrictSUIDSGID`, `RestrictNamespaces`, empty `CapabilityBoundingSet`,
  `SystemCallFilter=@system-service` + the `~@privileged …` deny-list.

Source units use `%h/Documents/Projects/jarvis/…` paths (sed-rewritten to `$INSTALL_DIR` by
`install.sh`) and `%h` for data paths, per the existing convention.

## Data flow

```
[timer 02:30] → jarvis-evolution-soak.service (env: GATE=1, TTFW=1000)
   → bin/jarvis-evolution soak
       → soak.previous_local_day_window_utc(now)        # (since_z, until_z)
       → ledger.reading_exists(since_z, until_z)?  yes → print "already logged", exit 0
       → backtest.score_window(since=since_z, until=until_z)   # reads turn_telemetry.db (ro)
       → ledger.append_reading(... window_start=since_z, window_end=until_z ...)   # GATE-gated
       → print one-line summary
[human, anytime] → bin/jarvis-evolution trend [--limit N]  → reads evolution_ledger.db (ro), prints table
```

## Testing

- `tests/test_evolution_soak.py`:
  - `test_previous_local_day_window_utc`: inject a fixed tz-aware `now`; assert the two `…Z`
    bounds (exact strings) and the `YYYY-MM-DDTHH:MM:SSZ` format.
  - `test_soak_idempotent`: run the soak window→dedup→append path twice against a tmp ledger;
    assert exactly one reading exists for the window.
  - `test_soak_writes_only_under_gate(monkeypatch)`: without `JARVIS_EVOLUTION_GATE`, the
    soak append is skipped (mirror `score --log`); with it set, one row is written.
- `tests/test_evolution_ledger.py` (extend):
  - `test_reading_exists_true_after_append` / `test_reading_exists_false_for_unlogged_window`.
  - The existing `test_append_only_no_mutation_api` must still pass (reading_exists is read-only).
- `trend` format smoke: build a tmp ledger with one reading, assert the rendered table contains
  the date and all five axis column headers.

## Verification (acceptance)

1. `cd src/voice-agent && .venv/bin/python -m pytest tests/test_evolution_*.py -q` → all pass.
2. `bin/jarvis-evolution soak` (no env) → computes + prints yesterday's reading, **writes
   nothing** ("GATE not set"). With `JARVIS_EVOLUTION_GATE=1 bin/jarvis-evolution soak` → logs one
   reading; a second run → "already logged", no duplicate.
3. `bin/jarvis-evolution trend` → prints the table including the just-logged day.
4. `.venv/bin/python -c "import jarvis_agent"` → still clean (evolution package has no
   import-time side effects and is not imported by the runtime).
5. Timer installed + enabled: `systemctl --user list-timers 'jarvis-evolution-soak*'` shows a
   next-fire; the unit is `setup/systemd/`-sourced + registered in `install.sh`.

## Out of scope (untouched — regression guard)

- The voice-agent runtime / any live path.
- `pipeline/automod/` and `HARD_BLOCKLIST_PATHS` — pinning `fitness.py` is a **separate later
  increment** with its own sign-off + spec amendment (`.claude/rules/regression-prevention.md §8`).
- `turn_telemetry.db` schema.
- The candidate generate→validate→apply loop (its own future spec; may only be built once this
  soak has run and the gate is trusted).
- The separate uncommitted `dispatch_agent`/`background_tasks` WIP in the working tree.

## Risks

- **Sparse/quiet days** — a day with few turns yields a low-`n` reading; the trend shows `n`
  so the user can discount thin days. Accepted (the gate already returns `passed False` for
  empty windows).
- **Gaps when the machine is off** — left visible as missing dates rather than backfilled
  (YAGNI). If gaps prove annoying during the soak, a `--backfill` extension is a trivial
  fast-follow.
- **Timezone/DST edge** — mitigated by computing in local tz via `astimezone()` and a unit test
  on the exact bounds; verified against the live DB format.
