# Plan 008: Clear the HIGH `form-data` npm advisory in the web app

> **Executor instructions**: Follow step by step, run each verify command, honor
> STOP conditions. Update this plan's row in `plans/README.md` when done.
>
> **Drift check (run first)**:
> `cd src/web && npm audit` — re-confirm the `form-data` HIGH advisory still
> shows before changing anything. If it's already gone, mark this plan DONE/REJECTED.

## Status

- **Priority**: P2
- **Effort**: S
- **Risk**: LOW (`npm audit fix` is the non-breaking patch path)
- **Depends on**: none
- **Category**: dependencies / security
- **Planned at**: commit `f6efd301`, 2026-06-22

## Why this matters

`cd src/web && npm audit` reports a **HIGH** advisory: `form-data 4.0.0–4.0.5`
CRLF injection (GHSA-hmw2-7cc7-3qxx), pulled transitively. The repo's
`security-audit.yml` gates the web tree at `--audit-level=high` (per
`docs/decisions-pending.md` #6), so a HIGH advisory is exactly what that gate is
meant to catch. `npm audit fix` resolves `form-data` **without** a breaking
change. This plan applies only that fix and verifies the tree still builds and
tests, leaving the unrelated moderate `esbuild`/`drizzle-kit` advisory alone
(below the gate; its fix is a breaking downgrade with low payoff — see notes).

## Current state

`cd src/web && npm audit` (confirmed at planning time) reports, among others:

```
form-data  4.0.0 - 4.0.5
Severity: high
form-data: CRLF injection ... GHSA-hmw2-7cc7-3qxx
fix available via `npm audit fix`

esbuild  <=0.24.2 || 0.27.3 - 0.28.0
Severity: moderate
... (via @esbuild-kit → drizzle-kit)
fix available via `npm audit fix --force`
Will install drizzle-kit@0.18.1, which is a breaking change
```

The web tree uses **bun** for build/test in CI but standard **npm** for the
lockfile audit (`npm audit` / `npm audit fix` operate on `package-lock.json`).
Check which lockfile(s) exist: `ls src/web/package-lock.json src/web/bun.lock src/web/bun.lockb 2>/dev/null`.

## Commands you will need

| Purpose | Command | Expected |
|---|---|---|
| Re-confirm advisory | `cd src/web && npm audit \| grep -A3 form-data` | shows the HIGH advisory (before) |
| Apply non-breaking fix | `cd src/web && npm audit fix` | updates form-data; exit 0 |
| Confirm cleared | `cd src/web && npm audit --audit-level=high` | no HIGH advisories (exit 0) |
| Build still green | `cd src/web && bun install && bun run build` | exit 0 |
| Tests still green | `cd src/web && npx vitest run` | no new failures |

## Scope

**In scope**:
- `src/web/package-lock.json` (and `package.json` only if `npm audit fix` bumps a
  direct dep range — review the diff)
- Whatever bun lockfile exists in `src/web/` IF it must be regenerated to match
  (see Step 3)

**Out of scope** (do NOT touch):
- `npm audit fix --force` / the `drizzle-kit` downgrade — the `esbuild` advisory
  is MODERATE (below the web gate), dev-only, and `--force` is a breaking change.
  Leave it; it's tracked separately.
- Any application source code.

## Git workflow

- Branch: `advisor/008-form-data-advisory`
- One commit, e.g. `chore(web): npm audit fix — clear form-data HIGH (GHSA-hmw2-7cc7-3qxx)`.
- Do NOT push / open a PR unless instructed.

## Steps

### Step 1: Confirm the advisory still applies

`cd src/web && npm audit | grep -A3 form-data`.
- If the HIGH `form-data` advisory shows → proceed.
- If it's gone (already patched) → set this plan REJECTED in the README with
  "form-data already patched" and stop.

### Step 2: Apply the non-breaking fix

`cd src/web && npm audit fix` (NOT `--force`).

**Verify**: `cd src/web && npm audit --audit-level=high` → exit 0, no HIGH
advisories. Review `git diff src/web/package.json` — it should be empty or a
minor transitive bump; if `npm audit fix` wants to change a DIRECT dependency
major version, STOP (see STOP conditions).

### Step 3: Reconcile the lockfile bun actually uses, then verify build + tests

CI builds with bun. If a bun lockfile exists, regenerate it so it matches the
patched tree, then build:

`cd src/web && bun install && bun run build`

**Verify**: `bun run build` exits 0; then `cd src/web && npx vitest run` shows no
new failures.

## Test plan

No new tests — this is a dependency patch. Verification IS: the advisory clears
(`npm audit --audit-level=high` exit 0) AND the build + existing suite stay green.

## Done criteria

- [ ] `cd src/web && npm audit --audit-level=high` exits 0 (no HIGH advisories).
- [ ] `cd src/web && bun run build` exits 0.
- [ ] `cd src/web && npx vitest run` → no new failures.
- [ ] `git diff` is limited to lockfile(s) (+ at most a minor `package.json` bump).
- [ ] `--force` / drizzle-kit was NOT run.
- [ ] `plans/README.md` row for 008 updated.

## STOP conditions

- `npm audit fix` proposes a breaking/major change to a DIRECT dependency (not
  just `form-data`) → STOP and report the proposed diff; don't accept it blind.
- The build or tests fail after the fix → STOP and report (revert the lockfile
  change: `git checkout -- src/web/package-lock.json` and any bun lockfile).
- `form-data` turns out to be reachable only via devDeps AND the security-audit
  gate runs with `--omit=dev` (so it wasn't actually failing CI) → still apply
  the fix (it's free), but note the corrected severity in the README row.

## Maintenance notes

- The `esbuild`/`drizzle-kit` MODERATE advisory remains by design (below the web
  `high` gate; `--force` downgrade not worth it). If `drizzle-kit` later ships a
  non-breaking fix, bump it then.
- Reviewer: confirm only lockfile/transitive changes, and that
  `npm audit --audit-level=high` is clean post-merge so the security-audit CI job
  goes green.
