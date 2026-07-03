# Plan 005: Shelve the evolution / automod loop

> **Executor instructions**: Follow step by step. Run every verification command
> and confirm the expected result before moving on. If any "STOP conditions" item
> occurs, stop and report — do not improvise. This plan REMOVES a large,
> entangled subsystem; the archive branch in Step 1 is mandatory (it is the undo).
> When done, update this plan's row in `advisor-plans/README.md`.
>
> **Drift check (run first)**:
> `git diff --stat 9861fd11..HEAD -- src/voice-agent/pipeline/automod src/voice-agent/tools/code_mod.py src/web/src/app/\(app\)/evolution`
> If these changed materially, re-map references (Step 2) before removing anything.

## Status

- **Priority**: P2
- **Effort**: L
- **Risk**: MED
- **Depends on**: none
- **Category**: tech-debt
- **Planned at**: commit `9861fd11`, 2026-07-02

## Why this matters

The evolution / automod loop is **34 modules / 7,414 LOC** in
`src/voice-agent/pipeline/automod/` (nearly the size of the entire agent
entrypoint) plus **54 test files**, a live registry tool (`propose_code_mod` in
`tools/code_mod.py`), **10 systemd units**, **11 `bin/` scripts**, and a whole
**web feature** (`/evolution` page + API + lib + hook). It is a self-modification
system that ships with a 3-layer blocklist defending the codebase *against
itself*. It runs in shadow (`JARVIS_AUTOMOD_SPAWN_LIVE` / `EVOLUTION_AUTOPUBLISH`
default `0`) and has never been used autonomously in production. For a single-user
personal assistant this is the highest-cost, lowest-return subsystem in the tree:
carried in full (code + tests + CI + docs + ops units) but not used. Shelving it
to an archive branch removes the maintenance, test-time, and cognitive load while
preserving the work if the maintainer ever wants to revive it.

This is a **shelve**, not a delete — Step 1 archives the current tree to a branch
so nothing is lost.

## Current state — the full touchpoint map

**Voice-agent (Python):**
- `src/voice-agent/pipeline/automod/` — 34 `.py` modules, 7,414 LOC (the loop:
  cycle, spawner, deploy, publish, watchdog, review_council, nightly, ondemand,
  finalize, `_state.py`, …).
- `src/voice-agent/tools/code_mod.py` — the LIVE `propose_code_mod` voice tool.
  It self-registers into the tool registry (via `tools/_adapter.py::load_all_livekit_tools`).
  **If `automod/` is removed but `code_mod.py` stays and imports it, the registry
  load fails and the voice agent will not boot.** Remove them together.
- ~54 test files under `src/voice-agent/tests/` matching `*automod*` / `*evolution*`.
- `setup/systemd/jarvis-voice-agent.service` and
  `setup/systemd/jarvis-voice-agent.system.service` each set
  `Environment=JARVIS_AUTOMOD_ENABLED=1` — remove that line (leave the rest of the
  unit intact).

**Systemd units to delete (10):** all of
`setup/systemd/jarvis-evolution-{nightly,soak,watchdog,introspect}.{service,timer}`.

**`bin/` scripts to delete (11):** `jarvis-automod`, `jarvis-automod-impl`,
`jarvis-automod-pytest`, `jarvis-dispatch-watchdog`, `jarvis-evolution`,
`jarvis-evolution-cycle`, `jarvis-evolution-introspect`, `jarvis-evolution-nightly`,
`jarvis-evolution-ondemand`, `jarvis-evolution-review`, `jarvis-evolution-watchdog`.

**Web feature to delete:**
- `src/web/src/app/(app)/evolution/` (page)
- `src/web/src/app/api/evolution/` (API routes)
- `src/web/src/lib/evolution/` (categorize.ts etc.)
- `src/web/src/hooks/use-evolution-count.ts`
- **Plus** any nav/sidebar link to `/evolution` — Step 4 greps for it (a dangling
  `<Link href="/evolution">` will break the build).

**Docs:**
- `CLAUDE.md` — the "Auto-mod loop is gated, audited, and reversible" section.
- `.claude/rules/regression-prevention.md` — rule **#8 "Auto-mod blocklist is
  load-bearing"** (the thing it protects is being removed).
- `.github/workflows/evolution-nightly.yml` — the CI regression job (delete).

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Archive branch | `git branch archive/evolution-loop-2026-07 HEAD` | branch created |
| Map python importers | `grep -rln "pipeline.automod\|from.*automod import\|import code_mod\|propose_code_mod" src/voice-agent --include='*.py'` | only automod/, code_mod.py, tests |
| Map web refs | `grep -rln "evolution" src/web/src` | only the files being deleted + nav link (Step 4) |
| Voice suite | `cd src/voice-agent && .venv/bin/python -m pytest tests/ -q` | all pass (after removals) |
| Web typecheck | `cd src/web && npx tsc --noEmit` | exit 0 |
| Web build | `cd src/web && npm run build` | build completes |

> Use `src/voice-agent/.venv/` for pytest.

## Scope

**In scope**: everything in the touchpoint map above.

**Out of scope** (do NOT touch):
- `src/voice-agent/sanitizers/`, `confab_detector.py` — unrelated safety layers.
- The auto-mod **blocklist enforcement in `bin/jarvis-automod merge`** is deleted
  WITH the loop; do not try to preserve it standalone.
- Any other tool in `tools/` — only `code_mod.py` is part of this subsystem.
- `pipeline/skill_review.py` — on the old blocklist but is a *separate* live
  feature (skill review), NOT part of automod. Leave it.

## Git workflow

- **Step 1 archive branch is mandatory and comes first.**
- Working branch: `advisor/005-shelve-evolution`
- Commit in logical units (voice removal / systemd+bin / web / docs), conventional
  style: `chore(evolution): shelve automod loop to archive branch`.
- Do NOT push or open a PR unless the operator asks. Do NOT delete the archive
  branch.

## Steps

### Step 1: Archive first (the undo path)
```
git branch archive/evolution-loop-2026-07 HEAD
git log -1 archive/evolution-loop-2026-07 --oneline
```
**Verify**: the archive branch exists at current HEAD. Everything removed below is
recoverable from it.

### Step 2: Confirm the Python coupling boundary
```
grep -rln "pipeline.automod\|from .*automod import\|import code_mod\|propose_code_mod" src/voice-agent --include='*.py'
```
**Verify**: matches fall ONLY within `pipeline/automod/`, `tools/code_mod.py`, and
`tests/`. If `pipeline.automod` or `code_mod` is imported by a **non-automod,
non-test** module (e.g. `jarvis_agent.py` or another `tools/*.py`) → note that
file; you will need to remove that import too. If the import is load-bearing for a
non-evolution feature → **STOP** and report.

### Step 3: Remove the voice-agent side
- `git rm -r src/voice-agent/pipeline/automod`
- `git rm src/voice-agent/tools/code_mod.py`
- `git rm src/voice-agent/tests/*automod* src/voice-agent/tests/*evolution*`
  (list them first with `ls`; delete exactly those).
- Remove any dangling `import`/registration of `code_mod` / `automod` found in
  Step 2 from non-test files.
- In BOTH `setup/systemd/jarvis-voice-agent.service` and
  `jarvis-voice-agent.system.service`, delete the line
  `Environment=JARVIS_AUTOMOD_ENABLED=1` (leave all other `Environment=` lines).

**Verify**:
- `grep -rn "automod\|propose_code_mod\|code_mod" src/voice-agent --include='*.py'` → no matches.
- `cd src/voice-agent && .venv/bin/python -c "import ast; ast.parse(open('jarvis_agent.py').read())"` → exit 0.
- `cd src/voice-agent && .venv/bin/python -m pytest tests/ -q` → **all pass**
  (this proves the registry still loads without `code_mod`/`automod`). If pytest
  errors on a missing `automod` import → you missed a reference in Step 2.

### Step 4: Remove the web feature (mind the nav link)
```
grep -rn "evolution" src/web/src --include='*.ts' --include='*.tsx'
```
Note every match. Then:
- `git rm -r "src/web/src/app/(app)/evolution" src/web/src/app/api/evolution src/web/src/lib/evolution src/web/src/hooks/use-evolution-count.ts`
- Remove any `<Link href="/evolution">` / nav entry / `use-evolution-count` import
  the grep surfaced (commonly in a sidebar or nav component). Leave unrelated code.

**Verify**:
- `grep -rn "evolution" src/web/src` → no matches (or only unrelated words — inspect).
- `cd src/web && npx tsc --noEmit` → exit 0 (a dangling import fails here).
- `cd src/web && npm run build` → completes (a dangling route/link fails here).

### Step 5: Remove ops units, bin scripts, CI, and docs
- `git rm setup/systemd/jarvis-evolution-*.service setup/systemd/jarvis-evolution-*.timer`
- `git rm bin/jarvis-automod bin/jarvis-automod-impl bin/jarvis-automod-pytest bin/jarvis-dispatch-watchdog bin/jarvis-evolution bin/jarvis-evolution-cycle bin/jarvis-evolution-introspect bin/jarvis-evolution-nightly bin/jarvis-evolution-ondemand bin/jarvis-evolution-review bin/jarvis-evolution-watchdog`
- `git rm .github/workflows/evolution-nightly.yml`
- `CLAUDE.md` — delete the "**Auto-mod loop is gated, audited, and reversible**"
  bullet/section (under "Active design decisions").
- `.claude/rules/regression-prevention.md` — delete rule **#8 "Auto-mod blocklist
  is load-bearing"** in its entirety.

**Verify**:
- `ls setup/systemd/ | grep evolution` → empty.
- `ls bin/ | grep -E 'automod|evolution'` → empty.
- `grep -rn "automod" CLAUDE.md .claude/rules/regression-prevention.md` → no matches.

### Step 6: Whole-tree sanity
**Verify**:
- `cd src/voice-agent && .venv/bin/python -m pytest tests/ -q` → all pass.
- `cd src/web && npx tsc --noEmit && npm run build` → both succeed.
- `git status` → only in-scope paths changed/deleted.

## Test plan

- No new tests — this is a removal. The two existing suites (voice pytest, web
  tsc+build) are the regression net and MUST stay green.
- The critical assertion is behavioral: the voice agent's tool registry still
  loads with `code_mod`/`automod` gone (proven by a green pytest, which imports the
  adapter) and the web app still builds with `/evolution` gone.

## Done criteria

ALL must hold:
- [ ] `archive/evolution-loop-2026-07` branch exists at the pre-removal HEAD
- [ ] `grep -rn "automod\|propose_code_mod" src/voice-agent CLAUDE.md .claude/rules bin setup/systemd` → no matches
- [ ] `grep -rn "evolution" src/web/src` → no matches (or only unrelated words)
- [ ] `cd src/voice-agent && .venv/bin/python -m pytest tests/ -q` exits 0
- [ ] `cd src/web && npx tsc --noEmit && npm run build` both succeed
- [ ] `git status` shows only in-scope files removed/edited
- [ ] `advisor-plans/README.md` status row updated

## STOP conditions

Stop and report (do not improvise) if:
- Step 2 shows `pipeline.automod` or `code_mod` imported by a live, non-evolution
  module you cannot cleanly remove.
- The voice suite fails on a **missing `automod` import** after Step 3 — a
  reference was missed; report it rather than stubbing modules back in.
- The web build fails and the cause is NOT a removed evolution import (i.e. the
  failure looks pre-existing/unrelated) — report; do not chase unrelated breakage.
- Removing rule #8 or the CLAUDE.md automod section appears to also govern a
  non-evolution feature. (`skill_review.py` shares the old blocklist but is NOT
  evolution — leave it; if the docs entangle them, report.)

## Maintenance notes

- The subsystem lives on `archive/evolution-loop-2026-07`. To revive: cherry-pick
  or merge that branch and re-add the systemd units + `JARVIS_AUTOMOD_ENABLED=1`.
- After this lands, the CD pipeline (`scripts/vps/deploy-poll.sh`) is the ONLY
  deploy path — the "autonomous self-improvement is one env-flip away" option is
  gone until the loop is revived. That is the intended trade for a personal box.
- Reviewer should scrutinize Step 4's nav-link removal (easiest thing to leave
  dangling) and confirm `pipeline/skill_review.py` was NOT deleted.
- The `.venv` and any local `~/.jarvis` automod state files are not in git; they
  can be left — they are inert without the code.
