# jarvis gh-agent — P2 (execute the task) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Make `jarvis gh-agent` actually *do* the task. When an allowlisted user comments `@jarvis <task>`, the agent clones the repo into a throwaway temp dir, runs `jarvis -p` headless to make the change, then: on an **issue** → pushes a new branch and opens a PR; on a **PR** → commits to that PR's branch and comments. Never auto-merges. Dry-run still posts/does nothing.

**Architecture:** New `task.ts` orchestrates one mention end-to-end via **injected runners** (`gh`, `git`, `exec`) so it's unit-testable with zero real side effects. `main.ts` swaps the P1 acknowledgement for `executeTask` on the non-dry-run path; all P1 machinery (allowlist gate, id-dedupe, cursor, self-marker) is unchanged. Isolation = fresh shallow clone in an OS temp dir (not the user's checkout, not a worktree), deleted in a `finally`.

**Tech Stack:** TypeScript, Bun (`bun:test`), `execFileNoThrow`, `gh` + `git` CLIs, the `jarvis -p` headless path.

**Spec:** `docs/superpowers/specs/2026-07-02-jarvis-gh-agent-design.md` (P2 phase). **Builds on:** P1 (`src/cli/src/gh-agent/`, committed).

**Decisions locked (from brainstorming):**
- Issue → open PR (base = default branch); PR → push to the PR's head branch + comment. Verified live: default branch is `master`; PR-vs-issue via `gh api repos/{repo}/issues/{n} --jq '.pull_request!=null'`; `gh pr view {n} --json headRefName`.
- `jarvis -p` runs autonomously (bypass) but only ever inside the throwaway clone. Per-task timeout (default 600s, config `executionTimeoutSec`). jarvis makes edits only — **P2 does all git/gh deterministically** (jarvis is told not to commit/push).
- No changes produced → comment "no changes were needed", no branch/PR.
- Never auto-merge. Self-marker `<!-- jarvis-gh-agent -->` on every PR/comment (P1's listMentions already filters it).

---

## File Structure

- Create `src/cli/src/gh-agent/task.ts` — `executeTask(repo, mention, cfg, deps)` + the injected-runner types + real-runner factory.
- Create `src/cli/src/gh-agent/task.test.ts`
- Modify `src/cli/src/gh-agent/config.ts` — add `executionTimeoutSec` (default 600) to `GhAgentConfig`/`DEFAULTS`/loader.
- Modify `src/cli/src/gh-agent/config.test.ts` — assert the new default.
- Modify `src/cli/src/gh-agent/main.ts` — call `executeTask` instead of `postComment` on the real path; keep dry-run + dedupe + cursor.
- Modify `src/cli/src/gh-agent/main.test.ts` — inject a stub executor; assert it's called for allowlisted mentions, not in dry-run, and a failure leaves the id un-handled + sets exitCode.

Run tests from `src/cli/`: `vendor/bun/linux-x64/bun test src/gh-agent/`. Parse: `vendor/bun/linux-x64/bun build <file> --no-bundle`.

---

## Task 1: config — add executionTimeoutSec

**Files:** Modify `src/cli/src/gh-agent/config.ts`, `src/cli/src/gh-agent/config.test.ts`

- [ ] **Step 1: Failing test** — add to `config.test.ts` inside the existing describe:

```ts
  test('executionTimeoutSec defaults to 600 and is overridable', () => {
    expect(DEFAULTS.executionTimeoutSec).toBe(600)
    const dir = mkdtempSync(join(tmpdir(), 'gha-'))
    const p = join(dir, 'gh-agent.json')
    writeFileSync(p, JSON.stringify({ executionTimeoutSec: 120 }))
    expect(loadGhAgentConfig(p).executionTimeoutSec).toBe(120)
    rmSync(dir, { recursive: true, force: true })
  })
```

- [ ] **Step 2: Run → fail** — `vendor/bun/linux-x64/bun test src/gh-agent/config.test.ts` → FAIL (`executionTimeoutSec` undefined).

- [ ] **Step 3: Implement** — in `config.ts`: add `executionTimeoutSec: number` to the `GhAgentConfig` type; add `executionTimeoutSec: 600` to `DEFAULTS`; in `loadGhAgentConfig` add:

```ts
      executionTimeoutSec: typeof raw.executionTimeoutSec === 'number' ? raw.executionTimeoutSec : DEFAULTS.executionTimeoutSec,
```

- [ ] **Step 4: Run → pass**; then full `bun test src/gh-agent/config.test.ts`.

- [ ] **Step 5: Commit**

```bash
git add src/cli/src/gh-agent/config.ts src/cli/src/gh-agent/config.test.ts
git commit -m "feat(cli): gh-agent config executionTimeoutSec (default 600)" -- src/cli/src/gh-agent/config.ts src/cli/src/gh-agent/config.test.ts
```

---

## Task 2: task.ts — execute one mention (issue→PR, PR→push)

**Files:** Create `src/cli/src/gh-agent/task.ts`, `src/cli/src/gh-agent/task.test.ts`

- [ ] **Step 1: Write the failing test**

```ts
// src/cli/src/gh-agent/task.test.ts
import { describe, expect, test } from 'bun:test'
import { executeTask, type TaskDeps } from './task.js'
import type { Mention } from './gh.js'
import { DEFAULTS } from './config.js'

const mention = (over: Partial<Mention> = {}): Mention => ({
  id: 1, body: '@jarvis add a hello file', author: 'ulrichando',
  createdAt: '2026-07-01T10:00:00Z', updatedAt: '2026-07-01T10:00:00Z',
  issueNumber: 12, url: 'u12', ...over,
})

// Records the ordered subprocess calls and lets each test script outcomes.
function harness(opts: {
  isPR?: boolean
  defaultBranch?: string
  headRef?: string
  producedChanges?: boolean
  execCode?: number
} = {}) {
  const calls: string[] = []
  const deps: TaskDeps = {
    gh: async (args) => {
      calls.push('gh ' + args.join(' '))
      if (args[0] === 'api' && /issues\/\d+$/.test(args[1] ?? '')) {
        return { stdout: JSON.stringify(opts.isPR ? { pull_request: { url: 'x' } } : {}), stderr: '', code: 0 }
      }
      if (args[0] === 'repo' && args[1] === 'view') return { stdout: opts.defaultBranch ?? 'master', stderr: '', code: 0 }
      if (args[0] === 'pr' && args[1] === 'view') return { stdout: opts.headRef ?? 'feature-x', stderr: '', code: 0 }
      if (args[0] === 'pr' && args[1] === 'create') return { stdout: 'https://github.com/o/r/pull/99', stderr: '', code: 0 }
      return { stdout: '', stderr: '', code: 0 } // pr comment
    },
    git: async (args) => {
      calls.push('git ' + args.join(' '))
      // `diff --cached --quiet`: exit 1 = changes present, 0 = none
      if (args.includes('diff') && args.includes('--quiet')) return { stdout: '', stderr: '', code: opts.producedChanges === false ? 0 : 1 }
      return { stdout: '', stderr: '', code: 0 }
    },
    exec: async () => { calls.push('exec jarvis -p'); return { stdout: 'done', stderr: '', code: opts.execCode ?? 0 } },
    makeTempDir: () => '/tmp/gha-test-clone',
    cleanup: () => { calls.push('cleanup') },
  }
  return { deps, calls }
}

describe('gh-agent executeTask', () => {
  test('ISSUE: clone → jarvis -p → commit → push new branch → open PR → cleanup', async () => {
    const { deps, calls } = harness({ isPR: false, defaultBranch: 'master', producedChanges: true })
    const r = await executeTask('o/r', mention(), DEFAULTS, deps)
    expect(r.ok).toBe(true)
    expect(r.prUrl).toBe('https://github.com/o/r/pull/99')
    const seq = calls.join('\n')
    expect(seq).toContain('gh repo clone o/r')
    expect(seq).toContain('exec jarvis -p')
    expect(seq).toContain('git -C /tmp/gha-test-clone push')
    expect(seq).toContain('gh pr create')
    expect(calls[calls.length - 1]).toBe('cleanup') // always cleans up
  })

  test('PR: commit to the PR head branch + comment, no pr create', async () => {
    const { deps, calls } = harness({ isPR: true, headRef: 'feat-y', producedChanges: true })
    const r = await executeTask('o/r', mention({ issueNumber: 20 }), DEFAULTS, deps)
    expect(r.ok).toBe(true)
    const seq = calls.join('\n')
    expect(seq).toContain('gh pr view 20')
    expect(seq).toContain('feat-y')          // checked out / pushed the PR branch
    expect(seq).toContain('gh pr comment 20')
    expect(seq).not.toContain('gh pr create')
    expect(calls[calls.length - 1]).toBe('cleanup')
  })

  test('no changes produced → comments "no changes", no branch/PR', async () => {
    const { deps, calls } = harness({ isPR: false, producedChanges: false })
    const r = await executeTask('o/r', mention(), DEFAULTS, deps)
    expect(r.ok).toBe(true)
    expect(r.noChanges).toBe(true)
    const seq = calls.join('\n')
    expect(seq).not.toContain('git -C /tmp/gha-test-clone push')
    expect(seq).not.toContain('gh pr create')
    expect(seq).toContain('gh api -X POST') // a comment was posted
    expect(calls[calls.length - 1]).toBe('cleanup')
  })

  test('jarvis -p failure → ok:false, no push, still cleans up', async () => {
    const { deps, calls } = harness({ isPR: false, execCode: 124 }) // timeout exit
    const r = await executeTask('o/r', mention(), DEFAULTS, deps)
    expect(r.ok).toBe(false)
    expect(calls.join('\n')).not.toContain('push')
    expect(calls[calls.length - 1]).toBe('cleanup')
  })
})
```

- [ ] **Step 2: Run → fail** — `vendor/bun/linux-x64/bun test src/gh-agent/task.test.ts` → FAIL (`Cannot find module './task.js'`).

- [ ] **Step 3: Implement**

```ts
// src/cli/src/gh-agent/task.ts
import { execFileNoThrow } from '../utils/execFileNoThrow.js'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import type { GhAgentConfig } from './config.js'
import type { Mention } from './gh.js'

const SELF_MARKER = '<!-- jarvis-gh-agent -->'

export type Runner = (args: string[]) => Promise<{ stdout: string; stderr: string; code: number }>
export type TaskDeps = {
  gh: Runner
  git: Runner            // caller passes full argv incl. `-C <dir>`
  exec: (cmd: string, cwd: string, timeoutSec: number) => Promise<{ stdout: string; stderr: string; code: number }>
  makeTempDir: () => string
  cleanup: (dir: string) => void
}
export type TaskResult = { ok: boolean; prUrl?: string; noChanges?: boolean; error?: string }

// Path to the jarvis launcher (this same CLI), used to run `jarvis -p` headless
// inside the clone. Repo-root bin/jarvis; resolved relative to this file's
// location (src/cli/src/gh-agent → repo root is five up).
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
function jarvisBin(): string {
  const here = dirname(fileURLToPath(import.meta.url))
  return resolve(here, '../../../../bin/jarvis')
}

export function realDeps(): TaskDeps {
  const run = (file: string): Runner => async (args) => {
    const r = await execFileNoThrow(file, args)
    return { stdout: r.stdout, stderr: r.stderr, code: r.code }
  }
  return {
    gh: run('gh'),
    git: run('git'),
    exec: async (cmd, cwd, timeoutSec) => {
      // Run jarvis headless in the clone; bypass mode is the source-path default.
      const r = await execFileNoThrow('bash', ['-lc', `cd ${JSON.stringify(cwd)} && ${cmd}`], { timeout: timeoutSec * 1000 })
      return { stdout: r.stdout, stderr: r.stderr, code: r.code }
    },
    makeTempDir: () => mkdtempSync(join(tmpdir(), 'jarvis-gh-')),
    cleanup: (dir) => { try { rmSync(dir, { recursive: true, force: true }) } catch { /* best effort */ } },
  }
}

function taskText(body: string, trigger: string): string {
  const i = body.indexOf(trigger)
  return (i === -1 ? body : body.slice(i + trigger.length)).trim()
}

export async function executeTask(
  repo: string,
  m: Mention,
  cfg: GhAgentConfig,
  deps: TaskDeps,
): Promise<TaskResult> {
  const dir = deps.makeTempDir()
  const gitc = (...a: string[]) => deps.git(['-C', dir, ...a])
  try {
    // 1. issue vs PR
    const info = await deps.gh(['api', `repos/${repo}/issues/${m.issueNumber}`, '--jq', '.pull_request'])
    const isPR = info.code === 0 && info.stdout.trim() !== '' && info.stdout.trim() !== 'null'

    // 2. clone (shallow) into the temp dir
    const clone = await deps.gh(['repo', 'clone', repo, dir, '--', '--depth', '1'])
    if (clone.code !== 0) return { ok: false, error: `clone failed: ${clone.stderr}` }

    // 3. position on the right branch
    let branch: string
    let defaultBranch = 'master'
    if (isPR) {
      const hr = await deps.gh(['pr', 'view', String(m.issueNumber), '--repo', repo, '--json', 'headRefName', '--jq', '.headRefName'])
      branch = hr.stdout.trim()
      // clone is shallow on the default branch; fetch + checkout the PR head
      await gitc('fetch', '--depth', '1', 'origin', branch)
      const co = await gitc('checkout', branch)
      if (co.code !== 0) return { ok: false, error: `checkout ${branch} failed: ${co.stderr}` }
    } else {
      const db = await deps.gh(['repo', 'view', repo, '--json', 'defaultBranchRef', '--jq', '.defaultBranchRef.name'])
      defaultBranch = db.stdout.trim() || 'master'
      branch = `jarvis/gh-${m.issueNumber}-${Date.now().toString(36)}`
      const co = await gitc('checkout', '-b', branch)
      if (co.code !== 0) return { ok: false, error: `branch create failed: ${co.stderr}` }
    }

    // 4. run jarvis -p headless (edits only; git is done here deterministically)
    const task = taskText(m.body, cfg.trigger)
    const prompt = `You are handling a GitHub request. Make ONLY the code changes for this task; do NOT run git commit, git push, or gh. Task: ${task}`
    const ex = await deps.exec(`${JSON.stringify(jarvisBin())} -p ${JSON.stringify(prompt)}`, dir, cfg.executionTimeoutSec)
    if (ex.code !== 0) return { ok: false, error: `jarvis -p exited ${ex.code}: ${ex.stderr.slice(0, 200)}` }

    // 5. stage + detect changes
    await gitc('add', '-A')
    const diff = await gitc('diff', '--cached', '--quiet') // code 1 = changes, 0 = none
    if (diff.code === 0) {
      await deps.gh(['api', '-X', 'POST', `repos/${repo}/issues/${m.issueNumber}/comments`, '-f',
        `body=No changes were needed for @${m.author}'s request: "${task}"\n\n${SELF_MARKER}`])
      return { ok: true, noChanges: true }
    }

    // 6. commit + push
    await gitc('-c', 'user.name=jarvis-gh-agent', '-c', 'user.email=jarvis@0wlan.com',
      'commit', '-m', `jarvis: ${task.slice(0, 60)}`)
    const push = await gitc('push', '-u', 'origin', isPR ? `HEAD:${branch}` : branch)
    if (push.code !== 0) return { ok: false, error: `push failed: ${push.stderr}` }

    // 7. issue → open PR; PR → comment
    if (isPR) {
      await deps.gh(['pr', 'comment', String(m.issueNumber), '--repo', repo, '--body',
        `Pushed changes for @${m.author}'s request to \`${branch}\`.\n\n${SELF_MARKER}`])
      return { ok: true }
    }
    const pr = await deps.gh(['pr', 'create', '--repo', repo, '--base', defaultBranch, '--head', branch,
      '--title', `jarvis: ${task.slice(0, 60)}`,
      '--body', `Closes #${m.issueNumber}. Requested by @${m.author}: "${task}"\n\n_Automated by jarvis gh-agent. Review before merge._\n\n${SELF_MARKER}`])
    if (pr.code !== 0) return { ok: false, error: `pr create failed: ${pr.stderr}` }
    return { ok: true, prUrl: pr.stdout.trim() }
  } finally {
    deps.cleanup(dir)
  }
}
```

- [ ] **Step 4: Run → pass** — `vendor/bun/linux-x64/bun test src/gh-agent/task.test.ts` (4 tests). Then parse: `vendor/bun/linux-x64/bun build src/gh-agent/task.ts --no-bundle`.

- [ ] **Step 5: Commit**

```bash
git add src/cli/src/gh-agent/task.ts src/cli/src/gh-agent/task.test.ts
git commit -m "feat(cli): gh-agent task executor (clone, jarvis -p, issue->PR / PR->push)" -- src/cli/src/gh-agent/task.ts src/cli/src/gh-agent/task.test.ts
```

---

## Task 3: wire executeTask into the sweep

**Files:** Modify `src/cli/src/gh-agent/main.ts`, `src/cli/src/gh-agent/main.test.ts`

- [ ] **Step 1: Update the test** — extend `RunOnceDeps` usage: `main.test.ts` should inject an `execute` stub and assert it's called for allowlisted mentions on the real path, skipped in dry-run, and a returned `{ok:false}` leaves the id un-handled + sets `process.exitCode`. Add:

```ts
  test('real run calls execute for allowlisted mention; failure leaves id unhandled', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'ghm-'))
    const { run } = recorder(comments)
    const seen: number[] = []
    let result = { ok: true as boolean }
    const execute = async (_repo: string, m: any) => { seen.push(m.id); return result }
    await runGhAgentOnce({ repo: 'o/r', dryRun: false }, { run, cfg: { ...DEFAULTS, allowlist: ['ulrichando'] }, cursorDir: dir, execute })
    expect(seen).toEqual([2]) // only the allowlisted author's mention
    rmSync(dir, { recursive: true, force: true })
  })

  test('dry-run does not call execute', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'ghm-'))
    const { run } = recorder(comments)
    let called = 0
    const execute = async () => { called++; return { ok: true } }
    await runGhAgentOnce({ repo: 'o/r', dryRun: true }, { run, cfg: { ...DEFAULTS, allowlist: ['ulrichando'] }, cursorDir: dir, execute })
    expect(called).toBe(0)
    rmSync(dir, { recursive: true, force: true })
  })
```

- [ ] **Step 2: Run → fail** — `execute` dep not supported yet.

- [ ] **Step 3: Implement** — in `main.ts`:
  - Add to `RunOnceDeps`: `execute?: (repo: string, m: Mention, cfg: GhAgentConfig) => Promise<{ ok: boolean; prUrl?: string; noChanges?: boolean; error?: string }>`.
  - At the top of the real-path handling, resolve the executor once: `const execute = deps.execute ?? (async (repo, m, cfg) => { const { executeTask, realDeps } = await import('./task.js'); return executeTask(repo, m, cfg, realDeps()) })`.
  - Replace the P1 ack block. For an allowlisted `m` on the non-dry-run path:

```ts
        const res = await execute(repo, m, cfg)
        if (res.ok) {
          log(`  #${m.issueNumber} ${res.noChanges ? 'no-changes' : (res.prUrl ? 'PR '+res.prUrl : 'pushed')} @${m.author}`)
          addHandledIds(repo, [m.id], deps.cursorDir)
        } else {
          log(`  #${m.issueNumber} FAILED @${m.author}: ${res.error ?? 'unknown'}`)
          process.exitCode = 1
          // do NOT mark handled → retried next sweep
        }
```

  - dry-run branch stays: `log('  #'+m.issueNumber+' DRY-RUN would handle @'+m.author+': "'+task+'"')` (unchanged), and still does NOT call execute or write state.
  - Keep the allowlist gate, the ignored→addHandledIds path, and the post-loop cursor advance exactly as P1.

- [ ] **Step 4: Run → pass** — `vendor/bun/linux-x64/bun test src/gh-agent/main.test.ts`, then the whole suite `vendor/bun/linux-x64/bun test src/gh-agent/` (all green), then parse `main.ts`.

- [ ] **Step 5: Commit**

```bash
git add src/cli/src/gh-agent/main.ts src/cli/src/gh-agent/main.test.ts
git commit -m "feat(cli): gh-agent sweep runs executeTask (P2) instead of ack" -- src/cli/src/gh-agent/main.ts src/cli/src/gh-agent/main.test.ts
```

---

## Task 4: dry-run smoke (safe) — HELD: live E2E is outward-facing

- [ ] **Step 1: Dry-run smoke** (posts/pushes nothing):

Run: `bin/jarvis gh-agent --repo ulrichando/jarvis --dry-run`
Expected: logs `N new mention(s)` and, per allowlisted `@jarvis` mention, `DRY-RUN would handle …`. No clone, no branch, no PR (dry-run never calls execute).

- [ ] **Step 2: Parse-check the command path** — `vendor/bun/linux-x64/bun build src/main.tsx --no-bundle` (exit 0).

- [ ] **HELD (do NOT run without explicit user go-ahead):** the real E2E opens a real branch + PR on the repo (outward-facing). When authorized: create a throwaway issue, comment `@jarvis add a file X with contents Y`, run `bin/jarvis gh-agent --repo ulrichando/jarvis` (real), verify a `jarvis/gh-*` branch + PR appear, review the PR, then close the issue/PR + delete the branch.

---

## Self-Review (completed)

- **Spec coverage (P2):** worktree/clone isolation (task.ts temp clone + finally cleanup), `jarvis -p` execution (exec runner + timeout), issue→PR vs PR→push (isPR branch), never-auto-merge (only comment/PR), dry-run unaffected (main.ts gate), self-marker on every post. P3 (daemon/timer deploy) deferred.
- **Placeholders:** none — full code + exact commands.
- **Type consistency:** `TaskDeps{gh,git,exec,makeTempDir,cleanup}`, `TaskResult{ok,prUrl?,noChanges?,error?}`, `executeTask(repo,m,cfg,deps)`, `RunOnceDeps.execute?` signature matches `executeTask`.
- **Safety:** allowlist gate (P1) unchanged and still upstream of execute; jarvis runs only in the throwaway clone; timeout bounds runaway; failed task not marked handled; no auto-merge; live E2E held.

## Next (P3, separate plan)

Deployment: a `jarvis-gh-agent.timer` (systemd `--user`) firing `jarvis gh-agent --once` every 1–2 min (timer > `--watch` daemon: crash-resilient, matches the fork's timer pattern), `maxTasksPerHour` enforcement, and a VPS variant (binary + timer + `gh` auth + proxy for 24/7). Documented, then optionally enabled.
