# Plan 011: Watchdog re-verifies health after a rollback before declaring success

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the next
> step. If anything in the "STOP conditions" section occurs, stop and report — do
> not improvise. When done, update the status row for this plan in
> `plans/README.md`.
>
> **Drift check (run first)**:
> `git diff --stat e04d31c8..HEAD -- src/voice-agent/pipeline/automod/watchdog.py src/voice-agent/tests/test_evolution_watchdog.py`
> If either changed since this plan was written, compare the "Current state"
> excerpts against the live code before proceeding; on a mismatch, STOP.

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: MED (safety-net code — additive new state, but review with care)
- **Depends on**: none
- **Category**: bug (correctness of the rollback safety net)
- **Planned at**: commit `e04d31c8`, 2026-06-27

## Why this matters

The external watchdog is the thing that lets JARVIS **survive a bad self-deploy**:
when a deploy is unhealthy past its window it does `git reset --hard <rollback_sha>`
+ restart. Today, the moment `_rollback()` returns `True`, the watchdog **clears
the deploy marker and declares "rolled-back"** — it never checks that the
rolled-back code actually came back healthy. The restart is asynchronous, so a
successful `git reset` does **not** mean a healthy agent. If the rollback target
(`rollback_sha`, the pre-deploy HEAD) is itself unhealthy — e.g. a regression had
already landed on master before the deploy — the agent stays dead, the marker is
gone, and **nothing rolls back or escalates further**. This plan keeps the marker
alive in a new `rolled-back-verifying` state and has the *next* watchdog tick run
the same health gate against the rolled-back code: heal → confirm + clear;
still-unhealthy past the window → escalate loudly (the last-good SHA is bad; a
human is needed) instead of silently believing the rollback worked.

## Current state

`src/voice-agent/pipeline/automod/watchdog.py` — the external safety net (runs in
its own process via `jarvis-evolution-watchdog.timer`). Relevant pieces:

- `BOOT_GRACE_S = 30`, `MAX_ROLLBACK_ATTEMPTS = 3` (module constants near top).
- Health signals already exist and are the ones to reuse:
  `_liveness()` (service active + `/status` connected + agent_present),
  `_real_turn_since(epoch)` (a non-error telemetry turn after `epoch`),
  `_smoke_turn()` (runs `pipeline.automod.selftest` as a subprocess, exit 0 = ok),
  `_parse_iso(ts)`, `_notify(event, **fields)`.
- The deploy module helpers used here: `_deploy.read_marker()`,
  `_deploy.write_marker(marker)`, `_deploy.clear_marker()`, `_deploy._now_iso()`,
  `_deploy.DEFAULT_DEADLINE_S`.
- `run_once()` opens like this:
  ```python
  @fault_boundary.supervised("watchdog_run_once", fallback="crashed")
  def run_once() -> str:
      marker = _deploy.read_marker()
      if not marker:
          return "no-marker"

      automod_id = marker.get("automod_id", "?")
      rollback_sha = marker.get("rollback_sha")
      deployed_at = _parse_iso(marker.get("deployed_at", "")) or time.time()
      ...
  ```
- The end of the rollback path (the lines to change) currently reads:
  ```python
      if _rollback(rollback_sha):
          try:
              from pipeline.automod import artifact
              artifact.update_status(automod_id, "auto-rolled-back",
                                     rolled_back_at=_deploy._now_iso(),
                                     rollback_sha=rollback_sha)
              artifact.audit("automod_deploy_rolled_back", id=automod_id,
                             rollback_sha=rollback_sha)
          except Exception:  # noqa: BLE001
              pass
          _notify("evolution_rolled_back", automod_id=automod_id,
                  rollback_sha=rollback_sha[:8],
                  detail="deploy was unhealthy; reverted to last-good + restarted")
          # ... (gated GitHub publish_rollback block) ...
          _deploy.clear_marker()        # <-- the two lines to replace
          return "rolled-back"
      # Reset failed — keep the marker so the next tick retries.
      return "rollback-failed"
  ```
- Existing marker `state` values seen elsewhere in the codebase: `pending-restart`,
  `watching`, `rolling-back`, `restart-failed`. The new value
  `rolled-back-verifying` does not collide with any of them, so old markers never
  hit the new branch.

The deploy marker is JSON at `~/.jarvis/auto-mods/active-deploy.json`
(`deploy.marker_path()`), honoring `JARVIS_HOME` — tests point `JARVIS_HOME` at a
tmp dir.

## Commands you will need

| Purpose | Command | Expected |
|---------|---------|----------|
| Watchdog tests | `cd src/voice-agent && .venv/bin/python -m pytest tests/test_evolution_watchdog.py -q` | all pass |
| Supervised-entrypoint test (watchdog is one) | `cd src/voice-agent && .venv/bin/python -m pytest tests/test_evolution_supervised_entrypoints.py -q` | all pass |
| Selftest still imports | `cd src/voice-agent && .venv/bin/python -m pytest tests/test_evolution_selftest.py -q` | all pass |

## Scope

**In scope**:
- `src/voice-agent/pipeline/automod/watchdog.py`
- `src/voice-agent/tests/test_evolution_watchdog.py` (add tests)

**Out of scope**:
- `deploy.py` — the deploy actuator needs no change (it writes the initial
  marker; the watchdog manages the rollback→verify transition).
- The rollback *mechanism* `_rollback()` (the `git reset` + restart) — unchanged.
- `MAX_ROLLBACK_ATTEMPTS` and the reset-failed retry path — unchanged. The verify
  state is reached only AFTER a reset *succeeded*; a failed reset still retries as
  today.

## Git workflow

- Branch off `master`: `git checkout -b advisor/011-watchdog-verify-rollback`.
- **NEVER `git add -A`.** Stage explicitly:
  `git add src/voice-agent/pipeline/automod/watchdog.py src/voice-agent/tests/test_evolution_watchdog.py`
  then `git commit -- <those paths>`; confirm with `git show --stat HEAD`.
- Conventional commit, e.g. `fix(automod): watchdog re-verifies health after rollback before clearing the marker`.
- **No `Co-Authored-By` / no Claude Code attribution.**

## Steps

### Step 1: Add the `_verify_rollback` helper

Add this function to `watchdog.py` (e.g. just above `run_once`). It runs the same
health gate against the rolled-back code, keyed on when the rollback happened.
```python
def _verify_rollback(marker: dict, automod_id: str) -> str:
    """Second-stage gate (OPS-04): after a rollback applied, confirm the
    rolled-back code is ACTUALLY healthy before believing the safety net worked.
    Heal → confirm + clear. Unhealthy past the window → escalate (the last-good
    SHA is bad; a human is needed) — never loop, re-rolling to the same SHA is
    pointless."""
    rollback_sha = marker.get("rollback_sha", "") or ""
    rolled_back_at = _parse_iso(marker.get("rolled_back_at", "")) or time.time()
    verify_deadline_s = int(marker.get("verify_deadline_s", _deploy.DEFAULT_DEADLINE_S))
    elapsed = time.time() - rolled_back_at

    if elapsed < BOOT_GRACE_S:
        return "rollback-boot-grace"

    if _liveness() and (_real_turn_since(rolled_back_at) or _smoke_turn()):
        _deploy.clear_marker()
        try:
            from pipeline.automod import artifact
            artifact.audit("automod_rollback_confirmed", id=automod_id,
                           rollback_sha=rollback_sha)
        except Exception:  # noqa: BLE001
            pass
        _notify("evolution_rollback_confirmed", automod_id=automod_id,
                rollback_sha=rollback_sha[:8],
                detail="rolled-back code is healthy")
        logger.info("[watchdog] rollback of %s CONFIRMED healthy", automod_id)
        return "rollback-healthy"

    if elapsed <= verify_deadline_s:
        return "rollback-verifying"

    # Past the verify window and still unhealthy: the rollback target itself is
    # bad. Escalate loudly + stop (do NOT re-roll to the same SHA).
    try:
        from pipeline.automod import artifact
        artifact.update_status(automod_id, "rollback-unhealthy",
                               rollback_sha=rollback_sha)
        artifact.audit("automod_rollback_unhealthy", id=automod_id,
                       rollback_sha=rollback_sha)
    except Exception:  # noqa: BLE001
        pass
    _notify("evolution_rollback_unhealthy", automod_id=automod_id,
            rollback_sha=rollback_sha[:8],
            detail="rolled back but STILL unhealthy — last-good SHA is bad; "
                   "manual intervention needed")
    logger.critical("[watchdog] rollback of %s did NOT restore health — escalating",
                    automod_id)
    _deploy.clear_marker()
    return "rollback-unhealthy"
```

### Step 2: Route the verify state at the top of `run_once`

Immediately after `automod_id = marker.get("automod_id", "?")`, intercept the new
state before any normal deploy-health logic:
```python
    automod_id = marker.get("automod_id", "?")
    # OPS-04: a prior tick rolled back and we're confirming the rolled-back code
    # is healthy. Handle BEFORE the normal deploy-health path.
    if marker.get("state") == "rolled-back-verifying":
        return _verify_rollback(marker, automod_id)
```

### Step 3: Transition into the verify state instead of clearing on rollback

In the rollback success block, replace the final `_deploy.clear_marker()` +
`return "rolled-back"` (the two lines marked in "Current state") with a marker
transition that keeps the marker for the next tick to verify:
```python
        marker["state"] = "rolled-back-verifying"
        marker["rolled_back_at"] = _deploy._now_iso()
        marker["verify_deadline_s"] = int(
            marker.get("deadline_s", _deploy.DEFAULT_DEADLINE_S)
        )
        _deploy.write_marker(marker)
        return "rolled-back"
```
Leave everything above it (the `artifact.update_status`/`audit`, the
`evolution_rolled_back` notify, and the gated GitHub `publish_rollback` block)
exactly as-is — those fire once, at the moment of rollback.

**Verify** (logic smoke, no service needed):
```
cd src/voice-agent && JARVIS_HOME="$(mktemp -d)" .venv/bin/python -c "
import time
from pipeline.automod import watchdog as w, deploy as d
# Simulate a marker mid-verify with a healthy agent.
d.write_marker({'automod_id':'automod-test','state':'rolled-back-verifying',
  'rollback_sha':'deadbeef','rolled_back_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(time.time()-60)),
  'verify_deadline_s': 300})
w._liveness = lambda: True
w._real_turn_since = lambda e: True
print('healthy ->', w.run_once())            # expect rollback-healthy
assert d.read_marker() is None, 'marker should be cleared on confirm'
# Unhealthy past the window -> escalate.
d.write_marker({'automod_id':'automod-test2','state':'rolled-back-verifying',
  'rollback_sha':'deadbeef','rolled_back_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(time.time()-9999)),
  'verify_deadline_s': 300})
w._liveness = lambda: False
w._real_turn_since = lambda e: False
w._smoke_turn = lambda: False
print('unhealthy ->', w.run_once())          # expect rollback-unhealthy
assert d.read_marker() is None, 'marker should be cleared on escalate'
print('OPS-04 smoke OK')
"
```
→ prints `healthy -> rollback-healthy`, `unhealthy -> rollback-unhealthy`, `OPS-04 smoke OK`.

### Step 4: Add tests to `test_evolution_watchdog.py`

**Read `src/voice-agent/tests/test_evolution_watchdog.py` first** and match its
existing harness (how it sets `JARVIS_HOME`, writes a marker, and monkeypatches
`_liveness` / `_smoke_turn` / `_real_turn_since`). Add tests covering:
- `test_rollback_verify_healthy_confirms_and_clears` — marker `rolled-back-verifying`,
  `rolled_back_at` ~60s ago, `_liveness` True + `_real_turn_since` True → `run_once`
  returns `"rollback-healthy"` and the marker is cleared.
- `test_rollback_verify_unhealthy_escalates` — same state, `rolled_back_at` well past
  `verify_deadline_s`, all health signals False → returns `"rollback-unhealthy"`,
  marker cleared, and an `evolution_rollback_unhealthy` record was written (assert
  via the same mechanism existing tests use for `_notify`/audit, or by reading
  `evolution_log_path()`).
- `test_rollback_verify_within_window_keeps_watching` — state set, unhealthy but
  `rolled_back_at` recent (within deadline, past boot grace) → returns
  `"rollback-verifying"` and the marker is NOT cleared.
- `test_unhealthy_deploy_transitions_to_verify_not_cleared` — drive the normal
  rollback path (unhealthy deploy past deadline) with `_rollback` monkeypatched to
  return True; assert `run_once` returns `"rolled-back"` AND the marker now has
  `state == "rolled-back-verifying"` (i.e. it was NOT cleared).

**Verify**: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_evolution_watchdog.py tests/test_evolution_supervised_entrypoints.py -q` → all pass, including the 4 new tests.

## Test plan

- New tests in `tests/test_evolution_watchdog.py`, modeled after its existing
  rollback/confirm tests (reuse their monkeypatch + marker-writing harness).
- Cases: confirm-after-rollback, escalate-after-rollback, still-verifying, and the
  rollback→verify transition (marker not cleared). The last one is the regression
  guard for the actual bug this plan fixes.
- Verification command above; both watchdog and supervised-entrypoint suites green.

## Done criteria

ALL must hold:

- [ ] `cd src/voice-agent && .venv/bin/python -m pytest tests/test_evolution_watchdog.py tests/test_evolution_supervised_entrypoints.py tests/test_evolution_selftest.py -q` exits 0
- [ ] The Step 3 inline smoke prints `rollback-healthy`, `rollback-unhealthy`, `OPS-04 smoke OK`
- [ ] `grep -n 'rolled-back-verifying' src/voice-agent/pipeline/automod/watchdog.py` shows it set in the rollback path AND read at the top of `run_once`
- [ ] `git show --stat HEAD` lists ONLY `watchdog.py` + the test file
- [ ] `plans/README.md` status row for 011 updated

## STOP conditions

- The "Current state" rollback block doesn't match the live `watchdog.py` (drift).
- A new state branch breaks an existing watchdog test in a way that isn't an
  obvious harness mismatch — STOP rather than weakening an existing assertion
  (these tests guard the deploy safety net).
- You find the existing tests already assert `run_once` returns `"rolled-back"`
  AND a cleared marker in the same step — that contract changes (marker now
  persists into verify); update those assertions to expect the verify state, and
  note it in your report.
- Verifying requires a live service restart — it does not; everything here is
  unit-testable with monkeypatched health signals. If you think you need a real
  restart, STOP (and remember the CLAUDE.md rule: never restart within 60s of the
  last `turn_telemetry.db` turn).

## Maintenance notes

- **`watchdog.py` is on the auto-mod `HARD_BLOCKLIST` and is the deploy safety
  net.** Human/normal executor only — the auto-mod loop is structurally forbidden
  from editing it (that's the whole point: the loop can't disable the thing that
  rolls it back).
- The verify state is terminal in both directions (confirm→clear, escalate→clear),
  so it cannot loop. It deliberately does NOT re-roll-back on a failed verify —
  the rollback target is the last-good SHA; if that's unhealthy, only a human can
  fix it.
- Follow-up deferred: wire `evolution_rollback_unhealthy` (and a persistently
  crashing `run_once`, which `fault_boundary` currently swallows to `"crashed"`)
  into the off-box `jarvis-notify` alert path so a dead safety net pages you. Out
  of scope here; tracked as a direction item from the 2026-06-27 Evolution review.
- A reviewer should confirm `_verify_rollback`'s health gate matches the deploy
  confirm gate's intent (liveness AND a fresh turn/smoke), and that the
  `verify_deadline_s` default (`DEFAULT_DEADLINE_S`, 300s) leaves enough time for a
  restart + warmup + one smoke turn.
