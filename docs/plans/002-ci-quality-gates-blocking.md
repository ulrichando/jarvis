# Plan 002: Web test + typecheck CI gates are blocking (not advisory)

> **Executor instructions**: Follow step by step. Run every verification command
> and confirm the expected result before the next step. Honor "STOP conditions".
> When done, update this plan's row in `plans/README.md`.
>
> **Drift check (run first)**:
> `git diff --stat f6efd301..HEAD -- .github/workflows/web-tests.yml .github/workflows/lint.yml`
> If either workflow changed, compare the excerpts in "Current state" against the
> live files before editing; on a mismatch, STOP.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: LOW (CI config; the escape hatch is "don't flip a gate that's red")
- **Depends on**: 001 (land the proxy gate tests first so the critical path is
  covered before the web-test gate goes hard)
- **Category**: dx / tests
- **Planned at**: commit `f6efd301`, 2026-06-22

## Why this matters

CI currently lets web tests and TypeScript type-checking **fail without failing
the build**: both steps carry `continue-on-error: true`. That defeats the repo's
own "verify before done" rule — a broken test or a type error merges green and
rots undetected. The comments say "ratchet to required once confirmed green";
memory indicates the suites are green now. This plan flips the two web gates to
blocking **only after verifying locally that they pass**, so CI starts catching
regressions without a false red.

## Current state

- `.github/workflows/web-tests.yml`, the `bun test` step (lines 42–46):
  ```yaml
        - name: bun test
          # web tests depend on better-sqlite3 native bindings; rebuilt by
          # bun install in CI but flip to required once confirmed green.
          continue-on-error: true
          run: bun test
  ```
- `.github/workflows/lint.yml`, the `tsc` job (lines 96–114):
  ```yaml
    tsc:
      name: tsc --noEmit (web)
      runs-on: ubuntu-latest
      # Non-blocking: type errors may exist today; ratchet to required once clean.
      continue-on-error: true
      ...
        - name: tsc --noEmit
          working-directory: src/web
          run: bunx tsc --noEmit
  ```

These are the ONLY two gates this plan touches. The same workflow has
`continue-on-error: true` on the **ruff** job (line 47) and the **clippy** job
(lines 66, 94) — those are deliberately left soft (ruff violations are
intentional and suppressed by `src/voice-agent/ruff.toml`; clippy warnings are a
separate cleanup). Do NOT flip ruff or clippy.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Web tests (local, runner CI uses) | `cd src/web && bun test` | exit 0, all pass |
| Web tests (vitest, package script) | `cd src/web && npx vitest run` | exit 0, all pass |
| Web typecheck | `cd src/web && bunx tsc --noEmit` | exit 0, no errors |

## Scope

**In scope**:
- `.github/workflows/web-tests.yml` — remove `continue-on-error: true` on the `bun test` step
- `.github/workflows/lint.yml` — remove `continue-on-error: true` on the `tsc` job

**Out of scope** (do NOT touch):
- The `ruff` and `clippy` jobs in `lint.yml` (intentionally soft — see above).
- Any application/source code. If tests or tsc are red, fixing them is NOT this
  plan's job (see STOP conditions) — report the failures instead.

## Git workflow

- Branch: `advisor/002-ci-gates-blocking`
- One commit, e.g. `ci(web): make bun test + tsc --noEmit blocking`.
- Do NOT push / open a PR unless instructed.

## Steps

### Step 1: Verify the web test suite is green locally

Run `cd src/web && bun test`. If it passes, also run `cd src/web && npx vitest run`
(the package's own runner) to confirm both runners agree.

- If **both pass** → proceed to Step 2.
- If `bun test` fails but `npx vitest run` passes (runner discrepancy), STOP and
  report which tests differ — flipping the CI `bun test` gate would red the build.
- If tests genuinely fail → STOP and report the failing test names. Do not fix.

**Verify**: `cd src/web && bun test` → exit 0.

### Step 2: Flip the web-test gate to blocking

In `.github/workflows/web-tests.yml`, delete the `continue-on-error: true` line
from the `bun test` step (and its now-stale trailing clause in the comment if you
like). Result:

```yaml
      - name: bun test
        # web tests depend on better-sqlite3 native bindings; rebuilt by bun install in CI.
        run: bun test
```

**Verify**: `grep -n 'continue-on-error' .github/workflows/web-tests.yml`
→ no output (the file has no remaining `continue-on-error`).

### Step 3: Verify typecheck is clean locally

Run `cd src/web && bunx tsc --noEmit`.

- If it exits 0 with no errors → proceed to Step 4.
- If it reports errors → STOP and report the **count and first 20 lines**. Do
  NOT attempt to fix type errors under this plan (unknown blast radius). Leave
  the `tsc` gate soft; still complete Steps 1–2 (the test gate is independent).

**Verify**: `cd src/web && bunx tsc --noEmit` → exit 0, no errors.

### Step 4: Flip the tsc gate to blocking

In `.github/workflows/lint.yml`, remove the `continue-on-error: true` line under
the `tsc:` job (line ~100). Leave the `ruff` and `clippy` jobs unchanged.

**Verify**: `sed -n '96,114p' .github/workflows/lint.yml` shows the `tsc` job no
longer has `continue-on-error: true`, while `grep -n 'continue-on-error'
.github/workflows/lint.yml` still shows the ruff/clippy entries.

## Test plan

No new tests. The verification IS running the gates locally (Steps 1, 3) before
flipping them. CI itself becomes the regression test going forward.

## Done criteria

- [ ] `cd src/web && bun test` exits 0 (confirmed in Step 1).
- [ ] `.github/workflows/web-tests.yml` has no `continue-on-error` (`grep` empty).
- [ ] EITHER `cd src/web && bunx tsc --noEmit` exits 0 AND the `tsc` job is now
      blocking, OR the tsc gate was left soft with a reported error count (note
      which in the README row).
- [ ] `ruff` and `clippy` jobs still carry `continue-on-error: true`.
- [ ] `git status` shows only the two workflow files changed.
- [ ] `plans/README.md` row for 002 updated.

## STOP conditions

- Web tests or tsc fail locally → STOP, report the failures, do not fix source.
- A runner discrepancy between `bun test` and `vitest` → STOP, report.
- The workflow files don't match the "Current state" excerpts → drift, STOP.

## Maintenance notes

- If tsc was left soft (Step 3 failed), a follow-up plan should fix the type
  errors and then flip the gate — track it.
- Once these gates are hard, a red CI genuinely blocks merge: keep `main`/`master`
  green or developers will be tempted to re-add `continue-on-error`.
- The `bun test` vs `vitest run` duality is a latent inconsistency (CI uses bun's
  runner, the package script uses vitest). Not in scope here, but worth unifying
  later so "green locally" and "green in CI" always mean the same suite.
