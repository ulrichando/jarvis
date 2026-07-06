# Plan 005: Web settings stored in `~/.jarvis` (not cwd-relative), with migration

> **Executor instructions**: Follow step by step, run each verify command, honor
> STOP conditions. Update this plan's row in `plans/README.md` when done.
>
> **Drift check (run first)**:
> `git diff --stat f6efd301..HEAD -- src/web/src/lib/settings/store.ts src/web/src/lib/knowledge/store.ts`
> If either changed, re-read before editing.

## Status

- **Priority**: P2
- **Effort**: S
- **Risk**: LOW (path change + read-fallback migration; no schema change)
- **Depends on**: none
- **Category**: tech-debt / migration
- **Planned at**: commit `f6efd301`, 2026-06-22

## Why this matters

`src/web/src/lib/settings/store.ts` writes `settings.json` under
`process.cwd()/.jarvis`, while the sibling knowledge and skills stores correctly
use `~/.jarvis`. So provider config and API keys live at a **cwd-dependent**
path: launch the web server from any directory other than `src/web` and the app
silently reads an empty settings file (keys "disappear"), and the user's config
is split from everything else in `~/.jarvis` (workspaces, knowledge, skills,
keys.env). This plan moves settings to `~/.jarvis` to match, with a one-time
read-fallback so the existing file isn't lost.

## Current state

- `src/web/src/lib/settings/store.ts:13-14` (the bug):
  ```ts
  const SETTINGS_DIR = path.join(process.cwd(), ".jarvis");
  const SETTINGS_FILE = path.join(SETTINGS_DIR, "settings.json");
  ```
  The module already imports `node:fs` (promises) and `node:path` but **not**
  `node:os`. `loadSettings()` (line 22) reads `SETTINGS_FILE`, falling back to
  `DEFAULT_SETTINGS` on any error. `saveSettings()` (line 34) calls `ensureDir()`
  then writes `SETTINGS_FILE`.
- The exemplar to match — `src/web/src/lib/knowledge/store.ts:1-14`:
  ```ts
  import os from "node:os";
  import path from "node:path";
  const KNOWLEDGE_DIR = path.join(os.homedir(), ".jarvis", "knowledge");
  ```
- There IS an existing file today at `src/web/.jarvis/settings.json` (it is
  modified in the current working tree). The migration must not discard it.

## Commands you will need

| Purpose | Command | Expected |
|---|---|---|
| Typecheck | `cd src/web && bunx tsc --noEmit` | exit 0 |
| Tests | `cd src/web && npx vitest run` | no new failures |
| Confirm new path is used | `grep -n 'os.homedir' src/web/src/lib/settings/store.ts` | shows the new constant |

## Scope

**In scope**:
- `src/web/src/lib/settings/store.ts` (path + migration fallback)
- `src/web/tests/settings-store.test.ts` (create — small migration test)

**Out of scope** (do NOT touch):
- `src/web/src/lib/knowledge/store.ts`, `lib/skills/store.ts` — already correct.
- Workspace index paths (`~/.jarvis/workspaces/_meta.json`) — unrelated and
  fragile; do not touch (see the memory note on workspace-index fragility).
- The settings SCHEMA (`lib/settings/schema.ts`) — no shape change.

## Git workflow

- Branch: `advisor/005-settings-storage-path`
- One commit, e.g. `fix(web): store settings under ~/.jarvis with migration`.
- Do NOT push / open a PR unless instructed.

## Steps

### Step 1: Point the store at `~/.jarvis`

In `src/web/src/lib/settings/store.ts`: add `import os from "node:os";` and change
the constants:

```ts
const SETTINGS_DIR = path.join(os.homedir(), ".jarvis");
const SETTINGS_FILE = path.join(SETTINGS_DIR, "settings.json");
// Legacy cwd-relative location (pre-2026-06). Read once for migration.
const LEGACY_SETTINGS_FILE = path.join(process.cwd(), ".jarvis", "settings.json");
```

**Verify**: `grep -n 'os.homedir\|LEGACY_SETTINGS_FILE' src/web/src/lib/settings/store.ts` → both present.

### Step 2: Add the one-time read-fallback migration

In `loadSettings()`, when the new `SETTINGS_FILE` is absent, fall back to the
legacy file (so an existing `src/web/.jarvis/settings.json` is honored). Keep the
parse + `DEFAULT_SETTINGS` fallback. Target shape:

```ts
export async function loadSettings(): Promise<Settings> {
  if (cache) return cache;
  for (const file of [SETTINGS_FILE, LEGACY_SETTINGS_FILE]) {
    try {
      const raw = await fs.readFile(file, "utf-8");
      const parsed = settingsSchema.safeParse(JSON.parse(raw));
      cache = parsed.success ? parsed.data : DEFAULT_SETTINGS;
      return cache;
    } catch {
      // try next location
    }
  }
  cache = DEFAULT_SETTINGS;
  return cache;
}
```

`saveSettings()` already writes the (now `~/.jarvis`) `SETTINGS_FILE`, so the next
save naturally migrates the data to the new location — no copy step needed.

**Verify**: `cd src/web && bunx tsc --noEmit` → exit 0.

### Step 3: Test the migration + new path

Create `src/web/tests/settings-store.test.ts`. Because the store reads
`os.homedir()`/`process.cwd()` at import, set `HOME`/`cwd` via `vi.stubEnv` /
`process.chdir` to temp dirs BEFORE a dynamic import, and `vi.resetModules()`
between cases (same reset-and-reimport pattern used for env-dependent modules).
Cases:
- **reads new-path file**: write `<tmpHome>/.jarvis/settings.json` (valid), import
  store, `loadSettings()` returns those values.
- **migrates from legacy**: new path absent, legacy `<tmpCwd>/.jarvis/settings.json`
  present → `loadSettings()` returns the legacy values; after `saveSettings(...)`
  the file now exists at the new path.
- **defaults when neither exists**: returns `DEFAULT_SETTINGS`.

Run vitest from `src/web`. If `process.chdir` is awkward under vitest, instead
inject paths by stubbing `HOME` only and asserting the homedir branch; keep the
legacy case as a focused check that the fallback loop tries both files.

**Verify**: `cd src/web && npx vitest run tests/settings-store.test.ts` → all pass.

## Test plan

- New `tests/settings-store.test.ts`: new-path read, legacy→new migration,
  defaults. Pattern: the reset-and-reimport approach (env read at module load).
- Verification: targeted vitest above, then `cd src/web && npx vitest run`
  (no new failures) and `bunx tsc --noEmit` (exit 0).

## Done criteria

- [ ] `store.ts` uses `os.homedir()` for `SETTINGS_DIR`; legacy path read as fallback.
- [ ] `cd src/web && bunx tsc --noEmit` exits 0.
- [ ] `tests/settings-store.test.ts` passes (incl. the legacy-migration case).
- [ ] `cd src/web && npx vitest run` → no new failures.
- [ ] `git status` shows only the two in-scope files.
- [ ] `plans/README.md` row for 005 updated.

## STOP conditions

- The code at `store.ts:13-14` doesn't match the excerpt (drift) → STOP.
- Other modules also compute `path.join(process.cwd(), ".jarvis", ...)` for
  files OTHER than settings (grep `process.cwd().*\.jarvis` across `src/web/src`)
  → STOP and report; unifying those is out of scope and may need its own plan.

## Maintenance notes

- After this lands, the live `src/web/.jarvis/settings.json` becomes legacy; it
  is read once and superseded on the next save to `~/.jarvis/settings.json`. The
  operator may delete the old `src/web/.jarvis/` afterward (note in the PR).
- Reviewer: confirm the migration is read-only on the legacy file (we never
  delete it) and that `saveSettings` writes only the new path.
