# JARVIS GitHub Action (v1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `@jarvis <task>` in a GitHub issue/PR comment triggers jarvis instantly via a GitHub Actions workflow, which runs the jarvis CLI on the runner and opens a PR.

**Architecture:** A new event-driven CLI subcommand `jarvis gh-action` (sibling of the poll-driven `jarvis gh-agent`) reads the GitHub Actions event JSON (`$GITHUB_EVENT_PATH`), guards on author association + trigger, runs `jarvis -p` in the already-checked-out `$GITHUB_WORKSPACE`, and publishes via the runner's `GITHUB_TOKEN`. A thin workflow YAML (`.github/workflows/jarvis.yml`) is the template users add to any repo. Model backend = self-contained: `bin/jarvis` auto-starts its proxy on the runner with provider keys from secrets (Option 1). Reuses `taskText` + publish shape from `gh-agent`.

**Tech Stack:** TypeScript/Bun (`vendor/bun/linux-x64/bun`), commander (CLI), GitHub Actions (composite/inline workflow), `gh` + `git` CLIs on the runner.

---

## Conventions (all tasks)

- Run tests/build from `src/cli/`: `vendor/bun/linux-x64/bun test src/gh-action/` and `vendor/bun/linux-x64/bun build <file> --no-bundle`.
- **Pathspec-only commits** (repo has 100+ dirty files from a parallel session): `git add <explicit paths> && git commit -m "…" -- <same paths>`, then `git show --stat HEAD` to confirm only your files. Never `git add -A`.
- Injected-deps pattern for testability (mirror `gh-agent/task.ts`): the orchestrator takes a `deps` object of thin runners so tests never touch the network/subprocess.
- Files in scope: `src/cli/src/gh-action/**`, `src/cli/src/main.tsx` (one command registration), `src/cli/scripts/start.sh` (skip-list), `.github/workflows/jarvis.yml`, `docs/runbook/jarvis-github-action.md`. Touch nothing else.

## File structure

- Create `src/cli/src/gh-action/event.ts` — parse `$GITHUB_EVENT_PATH` + env → normalized `ActionEvent`.
- Create `src/cli/src/gh-action/main.ts` — `runGhActionOnce(deps)`: guard → neutralize `.claude` → run jarvis → publish.
- Create `src/cli/src/gh-action/event.test.ts`, `src/cli/src/gh-action/main.test.ts`.
- Modify `src/cli/src/main.tsx` — register `jarvis gh-action`.
- Modify `src/cli/scripts/start.sh` — add `gh-action` to the commander skip-list.
- Create `.github/workflows/jarvis.yml` — workflow template.
- Create `docs/runbook/jarvis-github-action.md` — setup runbook.

---

### Task 1: Event parsing (`event.ts`)

**Files:** Create `src/cli/src/gh-action/event.ts`, Test `src/cli/src/gh-action/event.test.ts`

- [ ] **Step 1: Write failing tests**

```ts
// src/cli/src/gh-action/event.test.ts
import { test, expect } from 'bun:test'
import { parseActionEvent } from './event.js'

const base = { repo: 'o/n', trigger: '@jarvis' }
function ctx(name: string, payload: unknown) {
  return { eventName: name, repo: base.repo, trigger: base.trigger, payload }
}

test('issue_comment with trigger → task extracted', () => {
  const e = parseActionEvent(ctx('issue_comment', {
    action: 'created',
    issue: { number: 7 },
    comment: { body: '@jarvis add a README', user: { login: 'ulrichando' }, author_association: 'OWNER' },
  }))
  expect(e).toEqual({ repo: 'o/n', issueNumber: 7, isPR: false, task: 'add a README', author: 'ulrichando', association: 'OWNER' })
})

test('issue_comment on a PR → isPR true', () => {
  const e = parseActionEvent(ctx('issue_comment', {
    action: 'created',
    issue: { number: 9, pull_request: { url: 'x' } },
    comment: { body: '@jarvis fix it', user: { login: 'ulrichando' }, author_association: 'MEMBER' },
  }))
  expect(e?.isPR).toBe(true)
})

test('no trigger → null', () => {
  const e = parseActionEvent(ctx('issue_comment', {
    action: 'created', issue: { number: 7 },
    comment: { body: 'just a normal comment', user: { login: 'x' }, author_association: 'OWNER' },
  }))
  expect(e).toBeNull()
})

test('unsupported event → null', () => {
  expect(parseActionEvent(ctx('push', {}))).toBeNull()
})

test('self comment (bot marker) → null (no trigger loop)', () => {
  const e = parseActionEvent(ctx('issue_comment', {
    action: 'created', issue: { number: 7 },
    comment: { body: '@jarvis done <!-- jarvis-gh-agent -->', user: { login: 'x' }, author_association: 'OWNER' },
  }))
  expect(e).toBeNull()
})
```

- [ ] **Step 2: Run tests, verify fail** — `vendor/bun/linux-x64/bun test src/gh-action/event.test.ts` → FAIL (module not found).

- [ ] **Step 3: Implement `event.ts`**

```ts
// src/cli/src/gh-action/event.ts
import { taskText } from '../gh-agent/task.js'   // reuse: trigger-strip + NUL/control-char strip
import { SELF_MARKER } from '../gh-agent/gh.js'

export type ActionEvent = {
  repo: string
  issueNumber: number
  isPR: boolean
  task: string
  author: string
  association: string
}

export type ActionCtx = { eventName: string; repo: string; trigger: string; payload: any }

// Build the ctx from the runner's environment (GITHUB_* + the event JSON file).
export function actionCtxFromEnv(readFile: (p: string) => string): ActionCtx | null {
  const eventName = process.env.GITHUB_EVENT_NAME ?? ''
  const repo = process.env.GITHUB_REPOSITORY ?? ''
  const path = process.env.GITHUB_EVENT_PATH ?? ''
  const trigger = process.env.JARVIS_GH_TRIGGER ?? '@jarvis'
  if (!eventName || !repo || !path) return null
  let payload: unknown
  try { payload = JSON.parse(readFile(path)) } catch { return null }
  return { eventName, repo, trigger, payload }
}

export function parseActionEvent(ctx: ActionCtx): ActionEvent | null {
  const { eventName, repo, trigger, payload } = ctx
  let body = '', author = '', association = '', issueNumber = 0, isPR = false
  if (eventName === 'issue_comment' || eventName === 'pull_request_review_comment') {
    const c = payload?.comment
    if (!c) return null
    body = c.body ?? ''; author = c.user?.login ?? ''; association = c.author_association ?? ''
    if (eventName === 'pull_request_review_comment') { issueNumber = payload?.pull_request?.number ?? 0; isPR = true }
    else { issueNumber = payload?.issue?.number ?? 0; isPR = !!payload?.issue?.pull_request }
  } else if (eventName === 'issues' && payload?.action === 'opened') {
    const i = payload?.issue
    if (!i) return null
    body = i.body ?? ''; author = i.user?.login ?? ''; association = i.author_association ?? ''; issueNumber = i.number ?? 0
  } else {
    return null
  }
  if (body.includes(SELF_MARKER)) return null                        // never react to our own posts
  const triggerRe = new RegExp(`(?<![\\w-])${trigger.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}(?![\\w-])`)
  if (!triggerRe.test(body)) return null
  const task = taskText(body, trigger)
  if (!task || issueNumber <= 0 || !author) return null
  return { repo, issueNumber, isPR, task, author, association }
}
```

- [ ] **Step 4: Run tests, verify pass.** Then `vendor/bun/linux-x64/bun build src/gh-action/event.ts --no-bundle`.

- [ ] **Step 5: Commit** — `git add src/cli/src/gh-action/event.ts src/cli/src/gh-action/event.test.ts && git commit -m "feat(gh-action): parse GitHub Actions event → normalized task" -- src/cli/src/gh-action/event.ts src/cli/src/gh-action/event.test.ts`

---

### Task 2: Authorization guard

**Files:** Modify `src/cli/src/gh-action/event.ts` (add `isAuthorized`), Test in `event.test.ts`

- [ ] **Step 1: Write failing tests**

```ts
import { isAuthorized } from './event.js'
test('OWNER/MEMBER/COLLABORATOR pass; NONE fails', () => {
  expect(isAuthorized('OWNER', [])).toBe(true)
  expect(isAuthorized('MEMBER', [])).toBe(true)
  expect(isAuthorized('COLLABORATOR', [])).toBe(true)
  expect(isAuthorized('NONE', [])).toBe(false)
  expect(isAuthorized('CONTRIBUTOR', [])).toBe(false)
})
test('explicit allowlist is an AND-narrowing on the login', () => {
  // when an allowlist is set, association must pass AND login must be listed
  expect(isAuthorized('OWNER', ['someoneelse'], 'ulrichando')).toBe(false)
  expect(isAuthorized('OWNER', ['ulrichando'], 'ulrichando')).toBe(true)
})
```

- [ ] **Step 2: Run, verify fail.**

- [ ] **Step 3: Implement**

```ts
// append to event.ts
const TRUSTED_ASSOC = new Set(['OWNER', 'MEMBER', 'COLLABORATOR'])
export function isAuthorized(association: string, allowlist: string[], login = ''): boolean {
  if (!TRUSTED_ASSOC.has(association)) return false
  if (allowlist.length > 0) return allowlist.some(a => a.toLowerCase() === login.toLowerCase())
  return true
}
```

- [ ] **Step 4: Run, verify pass** + parse-check.

- [ ] **Step 5: Commit** — `feat(gh-action): author-association authorization guard`

---

### Task 3: Orchestrator (`main.ts`)

**Files:** Create `src/cli/src/gh-action/main.ts`, Test `src/cli/src/gh-action/main.test.ts`

Behavior: parse event → unmatched/unauthorized ⇒ log + return `{skipped}` (exit 0); else neutralize the target repo's `.claude/` (security), run `jarvis -p` in `$GITHUB_WORKSPACE` (hooks off), then `git`/`gh` publish — branch `jarvis/gh-<n>-<ts>`, commit, push, open PR (issue) / push+comment (PR). All external effects go through injected `deps`.

- [ ] **Step 1: Write failing tests** (stubbed deps; assert control flow, no real subprocess)

```ts
// src/cli/src/gh-action/main.test.ts
import { test, expect } from 'bun:test'
import { runGhActionOnce } from './main.js'

function deps(over: Partial<any> = {}) {
  const calls: string[] = []
  return { calls, d: {
    readEvent: () => ({ repo: 'o/n', issueNumber: 5, isPR: false, task: 'add X', author: 'ulrichando', association: 'OWNER' }),
    allowlist: [] as string[],
    workspace: '/ws',
    neutralizeClaude: (ws: string) => { calls.push(`neutralize:${ws}`) },
    exec: async () => { calls.push('jarvis'); return { code: 0, stdout: '', stderr: '' } },
    git: async (a: string[]) => { calls.push(`git:${a.join(' ')}`); return { code: a.includes('--quiet') ? 1 : 0, stdout: '', stderr: '' } },
    gh: async (a: string[]) => { calls.push(`gh:${a[0]} ${a[1] ?? ''}`); return { code: 0, stdout: 'https://pr', stderr: '' } },
    log: () => {}, dryRun: false, ...over,
  } }
}

test('authorized issue → runs jarvis, opens PR', async () => {
  const { calls, d } = deps()
  const r = await runGhActionOnce(d)
  expect(r.ok).toBe(true)
  expect(calls).toContain('jarvis')
  expect(calls.some(c => c.startsWith('gh:pr create'))).toBe(true)
  expect(calls[0]).toBe('neutralize:/ws')          // security step runs BEFORE jarvis
})

test('unauthorized (NONE) → skips, never runs jarvis', async () => {
  const { calls, d } = deps({ readEvent: () => ({ repo: 'o/n', issueNumber: 5, isPR: false, task: 'x', author: 'e', association: 'NONE' }) })
  const r = await runGhActionOnce(d)
  expect(r.skipped).toBe(true)
  expect(calls).not.toContain('jarvis')
})

test('no event (null) → skips cleanly', async () => {
  const { calls, d } = deps({ readEvent: () => null })
  const r = await runGhActionOnce(d)
  expect(r.skipped).toBe(true)
  expect(calls.length).toBe(0)
})

test('fork PR head is refused before jarvis', async () => {
  const { calls, d } = deps({ readEvent: () => ({ repo: 'o/n', issueNumber: 5, isPR: true, task: 'x', author: 'ulrichando', association: 'OWNER' }),
    prIsUntrusted: async () => true })
  const r = await runGhActionOnce(d)
  expect(r.skipped).toBe(true)
  expect(calls).not.toContain('jarvis')
})

test('dry-run posts/pushes nothing', async () => {
  const { calls, d } = deps({ dryRun: true })
  await runGhActionOnce(d)
  expect(calls).not.toContain('jarvis')
  expect(calls.some(c => c.startsWith('gh:'))).toBe(false)
})
```

- [ ] **Step 2: Run, verify fail.**

- [ ] **Step 3: Implement `main.ts`.** Note the publish half mirrors `gh-agent/task.ts` steps 5–7 (add → diff → commit → push → PR/comment) — reuse that shape. `deps.exec` runs `bin/jarvis -p <prompt>` (the jarvis binary path resolved by the workflow and passed in). `prIsUntrusted` calls `gh pr view --json isCrossRepository,author` (default provided in `realActionDeps`).

```ts
// src/cli/src/gh-action/main.ts
import { readFileSync, existsSync, renameSync } from 'node:fs'
import { join } from 'node:path'
import { execFileNoThrow, execFileNoThrowWithCwd } from '../utils/execFileNoThrow.js'
import { actionCtxFromEnv, parseActionEvent, isAuthorized, type ActionEvent } from './event.js'
import { SELF_MARKER } from '../gh-agent/gh.js'

export type ActionDeps = {
  readEvent: () => ActionEvent | null
  allowlist: string[]
  workspace: string
  jarvisBin?: string
  timeoutSec?: number
  neutralizeClaude: (ws: string) => void
  exec: (prompt: string) => Promise<{ code: number; stdout: string; stderr: string }>
  git: (args: string[]) => Promise<{ code: number; stdout: string; stderr: string }>
  gh: (args: string[]) => Promise<{ code: number; stdout: string; stderr: string }>
  prIsUntrusted?: (repo: string, n: number, allowlist: string[]) => Promise<boolean>
  log: (m: string) => void
  dryRun: boolean
}

export type ActionResult = { ok?: boolean; skipped?: boolean; prUrl?: string; error?: string; noChanges?: boolean }

export async function runGhActionOnce(d: ActionDeps): Promise<ActionResult> {
  const ev = d.readEvent()
  if (!ev) { d.log('gh-action: no matching @jarvis event'); return { skipped: true } }
  if (!isAuthorized(ev.association, d.allowlist, ev.author)) {
    d.log(`gh-action: @${ev.author} (${ev.association}) not authorized — skipping`); return { skipped: true }
  }
  if (ev.isPR && d.prIsUntrusted && await d.prIsUntrusted(ev.repo, ev.issueNumber, d.allowlist)) {
    d.log('gh-action: untrusted PR head — refusing'); return { skipped: true }
  }
  if (d.dryRun) { d.log(`gh-action DRY-RUN would handle #${ev.issueNumber}: "${ev.task}"`); return { skipped: true } }

  d.neutralizeClaude(d.workspace)                                   // SECURITY: drop target repo's hooks/settings
  const prompt = `You are handling a GitHub request. Make ONLY the code changes for this task; do NOT run git commit, git push, or gh. Task: ${ev.task}`
  const ex = await d.exec(prompt)
  if (ex.code !== 0) return { ok: false, error: `jarvis -p exited ${ex.code}: ${ex.stderr.slice(0, 200)}` }

  const gitc = d.git
  const add = await gitc(['add', '-A']); if (add.code !== 0) return { ok: false, error: `git add failed: ${add.stderr}` }
  const diff = await gitc(['diff', '--cached', '--quiet'])
  if (diff.code === 0) { await d.gh(['pr', 'comment', String(ev.issueNumber), '--repo', ev.repo, '--body', `No changes were needed for @${ev.author}'s request.\n\n${SELF_MARKER}`]); return { ok: true, noChanges: true } }
  if (diff.code !== 1) return { ok: false, error: `git diff failed: ${diff.stderr}` }

  const subject = ev.task.split('\n')[0]!.slice(0, 60)
  const branch = ev.isPR ? '' : `jarvis/gh-${ev.issueNumber}-${Date.now().toString(36)}`
  if (!ev.isPR) { const co = await gitc(['checkout', '-b', branch]); if (co.code !== 0) return { ok: false, error: `branch: ${co.stderr}` } }
  const c = await gitc(['-c', 'user.name=jarvis-gh-action', '-c', 'user.email=jarvis@0wlan.com', 'commit', '-m', `jarvis: ${subject}`])
  if (c.code !== 0) return { ok: false, error: `commit failed: ${c.stderr}` }
  const push = await gitc(['push', '-u', 'origin', ev.isPR ? 'HEAD' : branch]); if (push.code !== 0) return { ok: false, error: `push failed: ${push.stderr}` }

  if (ev.isPR) { await d.gh(['pr', 'comment', String(ev.issueNumber), '--repo', ev.repo, '--body', `Pushed changes for @${ev.author}.\n\n${SELF_MARKER}`]); return { ok: true } }
  const base = (await d.gh(['repo', 'view', ev.repo, '--json', 'defaultBranchRef', '--jq', '.defaultBranchRef.name'])).stdout.trim() || 'main'
  const pr = await d.gh(['pr', 'create', '--repo', ev.repo, '--base', base, '--head', branch, '--title', `jarvis: ${subject}`,
    '--body', `Closes #${ev.issueNumber}. Requested by @${ev.author}: "${ev.task}"\n\n_Automated by @jarvis. Review before merge._\n\n${SELF_MARKER}`])
  if (pr.code !== 0) return { ok: false, error: `pr create failed: ${pr.stderr}` }
  return { ok: true, prUrl: pr.stdout.trim() }
}

// Real deps for the runner. `neutralizeClaude` renames the target repo's .claude
// so its hooks/settings can't hijack the agent (ephemeral runner, safe).
export function realActionDeps(): ActionDeps {
  const ws = process.env.GITHUB_WORKSPACE ?? process.cwd()
  const jarvisBin = process.env.JARVIS_BIN ?? 'jarvis'
  const timeoutSec = Number(process.env.JARVIS_GH_TIMEOUT ?? '600')
  const run = (file: string) => async (args: string[]) => { const r = await execFileNoThrow(file, args); return { code: r.code, stdout: r.stdout, stderr: r.stderr } }
  return {
    readEvent: () => { const ctx = actionCtxFromEnv(p => readFileSync(p, 'utf8')); return ctx ? parseActionEvent(ctx) : null },
    allowlist: (process.env.JARVIS_GH_ALLOWLIST ?? '').split(',').map(s => s.trim()).filter(Boolean),
    workspace: ws, jarvisBin, timeoutSec,
    neutralizeClaude: (dir) => { const p = join(dir, '.claude'); if (existsSync(p)) { try { renameSync(p, join(dir, '.claude.untrusted')) } catch { /* best effort */ } } },
    exec: async (prompt) => { const r = await execFileNoThrowWithCwd(jarvisBin, ['-p', prompt], { cwd: ws, timeout: timeoutSec * 1000, env: { ...process.env, JARVIS_SKIP_VERIFY: '1' } }); return { code: r.code, stdout: r.stdout, stderr: r.stderr } },
    git: (args) => run('git')(['-C', ws, ...args]),
    gh: run('gh'),
    prIsUntrusted: async (repo, n, allowlist) => {
      const r = await execFileNoThrow('gh', ['pr', 'view', String(n), '--repo', repo, '--json', 'isCrossRepository,author'])
      if (r.code !== 0) return true
      try { const v = JSON.parse(r.stdout); return !!v.isCrossRepository || (allowlist.length > 0 && !allowlist.some((a: string) => a.toLowerCase() === (v.author?.login ?? '').toLowerCase())) } catch { return true }
    },
    log: (m) => console.log(`[gh-action] ${m}`),
    dryRun: process.argv.includes('--dry-run') || process.env.JARVIS_GH_DRY_RUN === '1',
  }
}
```

- [ ] **Step 4: Run tests, verify pass.** Confirm `execFileNoThrowWithCwd` accepts an `env` option — if not, extend its options type (it already passes `shell`; add `env?: NodeJS.ProcessEnv` threaded to execa). Parse-check both files.

- [ ] **Step 5: Commit** — `feat(gh-action): orchestrator — guard, neutralize hooks, run jarvis, publish`

---

### Task 4: CLI registration + launcher skip-list

**Files:** Modify `src/cli/src/main.tsx`, `src/cli/scripts/start.sh`

- [ ] **Step 1: Register the command** (near the `gh-agent` registration in `main.tsx`):

```ts
program
  .command("gh-action")
  .description("Run jarvis on a GitHub Actions @jarvis event (used by the jarvis workflow)")
  .option("--dry-run", "Log what would happen; post nothing")
  .action(async () => {
    const { runGhActionOnce, realActionDeps } = await import("./gh-action/main.js");
    const r = await runGhActionOnce(realActionDeps());
    if (r.error) { console.error(`[gh-action] FAILED: ${r.error}`); process.exitCode = 1; }
    process.exit(process.exitCode ?? 0);
  });
```

- [ ] **Step 2: Add `gh-action` to the skip-list** in `start.sh` (the `case "${1:-}"` at ~line 89) so commander-parsed flags aren't clobbered — insert `gh-action` alongside `gh-agent`.

- [ ] **Step 3: Verify** — `vendor/bun/linux-x64/bun build src/main.tsx --no-bundle` and `bin/jarvis gh-action --dry-run` (with no GITHUB_* env) prints "no matching @jarvis event" and exits 0.

- [ ] **Step 4: Commit** — `feat(gh-action): register jarvis gh-action command + launcher skip-list`

---

### Task 5: The workflow (`.github/workflows/jarvis.yml`)

**Files:** Create `.github/workflows/jarvis.yml`

- [ ] **Step 1: Write the workflow.** v1 install = clone+build jarvis with a read-scoped token secret (`JARVIS_CLI_TOKEN`). Provider keys as secrets (Anthropic shown; add others as needed). The author gate is the `if:`.

```yaml
name: jarvis
on:
  issue_comment:
    types: [created]
  pull_request_review_comment:
    types: [created]
  issues:
    types: [opened]

permissions:
  contents: write
  pull-requests: write
  issues: write

jobs:
  jarvis:
    # Primary gate: trigger present AND author is trusted. (Belt-and-suspenders
    # re-checked in the CLI.) `issues` payload nests author_association differently.
    if: >
      (github.event.comment && contains(github.event.comment.body, '@jarvis') &&
        (github.event.comment.author_association == 'OWNER' ||
         github.event.comment.author_association == 'MEMBER' ||
         github.event.comment.author_association == 'COLLABORATOR')) ||
      (github.event.issue && github.event.action == 'opened' &&
        contains(github.event.issue.body, '@jarvis') &&
        (github.event.issue.author_association == 'OWNER' ||
         github.event.issue.author_association == 'MEMBER' ||
         github.event.issue.author_association == 'COLLABORATOR'))
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: oven-sh/setup-bun@v2
      - name: Install jarvis CLI
        run: |
          git clone --depth 1 "https://x-access-token:${JARVIS_CLI_TOKEN}@github.com/ulrichando/jarvis.git" "$RUNNER_TEMP/jarvis"
          cd "$RUNNER_TEMP/jarvis/src/cli" && bun install --frozen-lockfile
        env:
          JARVIS_CLI_TOKEN: ${{ secrets.JARVIS_CLI_TOKEN }}
      - name: Run jarvis
        run: "$RUNNER_TEMP/jarvis/bin/jarvis gh-action"
        env:
          JARVIS_BIN: ${{ runner.temp }}/jarvis/bin/jarvis
          GITHUB_TOKEN: ${{ github.token }}
          GH_TOKEN: ${{ github.token }}
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          GROQ_API_KEY: ${{ secrets.GROQ_API_KEY }}
          JARVIS_GH_TRIGGER: '@jarvis'
```

- [ ] **Step 2: Lint** — run `actionlint` if available (`docker run --rm -v "$PWD":/repo rhysd/actionlint -color /repo/.github/workflows/jarvis.yml`) OR `python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/jarvis.yml'))"` for at least YAML validity. Expected: no errors.

- [ ] **Step 3: Commit** — `feat(gh-action): jarvis.yml workflow template (webhook @jarvis trigger)`

---

### Task 6: Runbook + smoke

**Files:** Create `docs/runbook/jarvis-github-action.md`

- [ ] **Step 1: Write the runbook** — how to enable `@jarvis` on a repo: (1) copy `.github/workflows/jarvis.yml` into the repo, (2) add secrets `JARVIS_CLI_TOKEN` (read-scoped PAT for `ulrichando/jarvis`) + `ANTHROPIC_API_KEY` (and any other provider keys), (3) comment `@jarvis <task>` on an issue. Document the author-association gate, the fork-head refusal, cost, and the "publish the binary to drop `JARVIS_CLI_TOKEN`" follow-up. Note it coexists with the poll-runner.

- [ ] **Step 2: Dry-run smoke** — `JARVIS_GH_DRY_RUN=1` with a hand-made event JSON:
  ```bash
  echo '{"action":"created","issue":{"number":1},"comment":{"body":"@jarvis hi","user":{"login":"ulrichando"},"author_association":"OWNER"}}' > /tmp/ev.json
  GITHUB_EVENT_NAME=issue_comment GITHUB_REPOSITORY=o/n GITHUB_EVENT_PATH=/tmp/ev.json bin/jarvis gh-action --dry-run
  ```
  Expected: logs a DRY-RUN line, exits 0, posts nothing.

- [ ] **Step 3: Commit** — `docs(gh-action): setup runbook`

- [ ] **HELD (human-run, outward-facing): live E2E** — add `jarvis.yml` + secrets to a throwaway repo, comment `@jarvis add HELLO.md`, confirm the workflow run opens a PR, then tear down. Do NOT run as part of implementation.

---

## Self-review notes

- **Spec coverage:** trigger/instant (Task 5 events), self-contained backend (workflow runs `bin/jarvis` which auto-starts the proxy from provider-key envs — Task 5), security (author gate Task 2/5, fork-head refusal Task 3, `.claude` neutralize Task 3), publish via `GITHUB_TOKEN` (Task 3/5), coexistence (separate module, reuses `taskText`). All covered.
- **Type consistency:** `ActionEvent`/`ActionDeps`/`ActionResult` are used identically across `event.ts`/`main.ts`/tests.
- **Known verification points (not placeholders — flagged for the implementer):** (1) `execFileNoThrowWithCwd` may need an `env` option added; (2) `issues.opened` `author_association` is often `NONE`/`OWNER` — the gate handles it; (3) `git push` on a runner relies on `actions/checkout` persisting the token (default true) — if a run fails to push, add `gh auth setup-git` or a token remote.
