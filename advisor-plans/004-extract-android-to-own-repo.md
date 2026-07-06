# Plan 004: Extract the Android app to its own repo

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving on. If any
> "STOP conditions" item occurs, stop and report — do not improvise. When done,
> update this plan's status row in `advisor-plans/README.md`.
>
> **Drift check (run first)**:
> `git diff --stat 9861fd11..HEAD -- src/android .github/workflows/android-smoke.yml`
> If these changed since this plan was written, re-verify the facts below before
> proceeding.

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: LOW
- **Depends on**: none
- **Category**: tech-debt
- **Planned at**: commit `9861fd11`, 2026-07-02

## Why this matters

`src/android/` is a **2,294-file, 146 MB** standalone on-device Kotlin/Compose
app. It is effectively parked — only 12 commits have ever touched it and the most
recent was a cleanup ("drop unused vendored llama.cpp tooling"). It is fully
self-contained (its own `gradlew`, `settings.gradle.kts`, `build.gradle.kts`) and
**nothing outside `src/android/` references it**. Yet it inflates this repo's file
count by ~40%, runs its own CI job, and was the source of 228 Dependabot alerts.
For a single-user personal-assistant project, keeping a parked second product in
the main monorepo is pure carrying cost. Splitting it to its own repo (history
preserved) removes that cost with zero impact on the daily-used voice/web/desktop
stack.

## Current state

- `src/android/` — standalone Gradle project. Top level contains `gradlew`,
  `settings.gradle.kts`, `build.gradle.kts`, `gradle.properties`, `app/`,
  `gradle/`, `scripts/`, `README.md`. Builds with **JDK 21** (not the default 25):
  `JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64 ./gradlew compileDebugKotlin`.
- `.github/workflows/android-smoke.yml` — the only CI that references it. Note:
  this workflow **SKIPS the NDK build** (documented), so it is a shallow check.
- **No cross-references**: `grep -rln "src/android"` across `*.md/*.yml/*.sh/*.ts`
  (excluding `src/android/` itself) returns nothing. The other subtrees do not
  import, launch, or build it.
- History depth: `git log --oneline -- src/android` = 12 commits. Small, clean
  history — a `git subtree split` produces a tidy standalone repo.

### Convention to follow
This repo uses `git` history-preserving operations for structural moves (see the
git-history-scrub runbook). Preserve Android's commit history in the new repo via
`git subtree split`; do NOT just copy files.

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Drift check | `git diff --stat 9861fd11..HEAD -- src/android` | (review any changes) |
| Confirm no cross-refs | `grep -rln "src/android" --include='*.md' --include='*.yml' --include='*.sh' --include='*.ts' . \| grep -v node_modules \| grep -v '^src/android/'` | no output |
| History split | `git subtree split --prefix=src/android -b android-standalone` | prints a commit SHA |
| Android still builds (in new repo) | `JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64 ./gradlew compileDebugKotlin` | BUILD SUCCESSFUL |
| Web/voice unaffected | (not needed — nothing imports android) | — |

## Scope

**In scope**:
- Create the new standalone repo from `src/android/` history (subtree split).
- In THIS repo: `git rm -r src/android`, delete
  `.github/workflows/android-smoke.yml`, remove any Android entry from
  `.github/dependabot.yml` (check first — Step 4), and remove Android mentions
  from `CLAUDE.md` (the stack table row).

**Out of scope** (do NOT touch):
- Any other subtree — nothing imports android, so nothing else needs editing.
- The blob history rewrite of THIS repo (i.e. do NOT `git filter-repo` to purge
  android blobs from past commits). A plain `git rm` is enough for a personal repo;
  full history purge is a separate, heavier decision the maintainer can make later.

## Git workflow

- New repo: push `android-standalone` branch to a fresh `jarvis-android` repo.
- This repo branch: `advisor/004-extract-android`
- Commit style: conventional, e.g. `chore(android): extract to standalone repo`.
- Do NOT push or create the GitHub repo without operator confirmation — creating a
  remote repo is an outward action (see STOP conditions).

## Steps

### Step 1: Verify self-containment (safety gate)
```
grep -rln "src/android" --include='*.md' --include='*.yml' --include='*.sh' --include='*.ts' . | grep -v node_modules | grep -v '^src/android/'
```
**Verify**: no output (nothing outside `src/android/` references it). If ANY line
appears → **STOP** and report which file couples to android; the removal is not
clean.

### Step 2: Produce the history-preserving split
```
git subtree split --prefix=src/android -b android-standalone
```
**Verify**: prints a commit SHA; `git log --oneline android-standalone | wc -l`
shows ~12 commits (Android's history, rooted at `src/android/` as repo root).

### Step 3: Create the standalone repo (operator-gated — see STOP)
Only after the operator confirms the new remote name:
```
# in a fresh working dir:
git clone <this-repo> jarvis-android && cd jarvis-android
git checkout android-standalone
git remote set-url origin <new jarvis-android remote>
# operator pushes; then verify the app builds from the new root:
JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64 ./gradlew compileDebugKotlin
```
**Verify**: `BUILD SUCCESSFUL` from the new repo root (proves the split is a
complete, buildable project).

### Step 4: Remove Android from this repo
- `git rm -r src/android`
- `git rm .github/workflows/android-smoke.yml`
- `grep -n "android" .github/dependabot.yml` — if an `android`/`gradle` ecosystem
  entry exists, remove that block; if none, no change.
- `CLAUDE.md` — remove the "Android app" row from the "Stack at a glance" table.

**Verify**:
- `git status` shows `src/android` deleted, the workflow deleted, docs edited.
- `grep -rln "src/android" . | grep -v node_modules` → no matches.
- `cd src/web && npx tsc --noEmit` → exit 0 (sanity: web untouched and still typechecks).

## Test plan

- No new tests. Android has no test coverage wired into this repo's suites.
- Regression net = "nothing else builds/imports android," proven by Step 1's grep
  and the web typecheck in Step 4.
- Post-split, the Android app's OWN tests live in the new repo and run there.

## Done criteria

ALL must hold:
- [ ] `android-standalone` branch exists with Android's full ~12-commit history
- [ ] The new repo builds: `./gradlew compileDebugKotlin` → BUILD SUCCESSFUL
- [ ] `grep -rln "src/android" . | grep -v node_modules` → no matches in this repo
- [ ] `.github/workflows/android-smoke.yml` deleted; dependabot android entry gone
- [ ] `cd src/web && npx tsc --noEmit` exits 0
- [ ] `advisor-plans/README.md` status row updated

## STOP conditions

Stop and report (do not improvise) if:
- Step 1 finds any cross-reference to `src/android` from another subtree.
- The subtree split produces a branch that does NOT build from its root.
- You are about to create a GitHub repo or push a new remote and the operator has
  not explicitly named the target repo — creating/pushing a remote is an outward
  action; confirm first.
- `git filter-repo` or any history-rewrite of THIS repo seems necessary — it is
  explicitly out of scope; report instead.

## Maintenance notes

- After this lands, Android issues/CI/dependabot live in the new repo. This repo's
  Dependabot alert surface drops substantially (Android/Gradle ecosystem gone).
- The Android blobs remain in THIS repo's git history (a plain `git rm`). If disk
  or clone size becomes a concern, a separate `git filter-repo` pass can purge
  them — deliberately deferred here (history rewrite + force-push is higher risk).
- If the maintainer later revives Android as the product direction, it is a clean
  standalone repo to iterate in, not a monorepo tenant.
