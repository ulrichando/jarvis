# jarvis gh-agent — P1 (poll + gate + acknowledge) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the safe foundation of the jarvis GitHub agent — `jarvis gh-agent --once` polls a repo for `@jarvis <task>` comments by allowlisted authors and posts an acknowledgement reply, advancing a per-repo cursor so nothing re-fires. No code execution, no pushes yet (that is P2).

**Architecture:** Four small units under `src/cli/src/gh-agent/` — `config` (load + allowlist gate), `cursor` (per-repo since-marker), `gh` (thin injectable wrappers over the authed `gh` CLI), `main` (one-sweep loop) — plus a `jarvis gh-agent` command in `main.tsx`. Everything reuses the machine's already-authed `gh`; no GitHub App/webhook.

**Tech Stack:** TypeScript, Bun (`bun test`, `bun:test`), the fork's `execFileNoThrow` wrapper, `gh` CLI.

**Spec:** `docs/superpowers/specs/2026-07-02-jarvis-gh-agent-design.md`

---

## File Structure

- Create `src/cli/src/gh-agent/config.ts` — config type, `loadGhAgentConfig`, `isAllowedAuthor`, path constants.
- Create `src/cli/src/gh-agent/config.test.ts`
- Create `src/cli/src/gh-agent/cursor.ts` — `readCursor`, `advanceCursor` (per-repo file).
- Create `src/cli/src/gh-agent/cursor.test.ts`
- Create `src/cli/src/gh-agent/gh.ts` — `GhRunner` type, `listMentions`, `postComment`.
- Create `src/cli/src/gh-agent/gh.test.ts`
- Create `src/cli/src/gh-agent/main.ts` — `runGhAgentOnce`.
- Create `src/cli/src/gh-agent/main.test.ts`
- Modify `src/cli/src/main.tsx` — register the `gh-agent` command (near the `keys` command, ~line 5962).

Verify parse for any changed `.ts` with:
`vendor/bun/linux-x64/bun build <file> --no-bundle` (from `src/cli/`).
Run tests with: `vendor/bun/linux-x64/bun test <file>` (from `src/cli/`).

---

## Task 1: config — load + allowlist gate

**Files:**
- Create: `src/cli/src/gh-agent/config.ts`
- Test: `src/cli/src/gh-agent/config.test.ts`

- [ ] **Step 1: Write the failing test**

```ts
// src/cli/src/gh-agent/config.test.ts
import { describe, expect, test } from 'bun:test'
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { loadGhAgentConfig, isAllowedAuthor, DEFAULTS } from './config.js'

describe('gh-agent config', () => {
  test('missing file → defaults (allowlist = ulrichando)', () => {
    const cfg = loadGhAgentConfig(join(tmpdir(), 'nope-gh-agent.json'))
    expect(cfg.allowlist).toEqual(['ulrichando'])
    expect(cfg.trigger).toBe('@jarvis')
    expect(cfg.pollSeconds).toBe(45)
    expect(cfg.repos).toEqual([])
  })

  test('file overrides merge over defaults', () => {
    const dir = mkdtempSync(join(tmpdir(), 'gha-'))
    const p = join(dir, 'gh-agent.json')
    writeFileSync(p, JSON.stringify({ repos: ['o/r'], allowlist: ['alice'], pollSeconds: 10 }))
    const cfg = loadGhAgentConfig(p)
    expect(cfg.repos).toEqual(['o/r'])
    expect(cfg.allowlist).toEqual(['alice'])
    expect(cfg.pollSeconds).toBe(10)
    expect(cfg.trigger).toBe('@jarvis') // still defaulted
    rmSync(dir, { recursive: true, force: true })
  })

  test('malformed JSON → defaults (never throws)', () => {
    const dir = mkdtempSync(join(tmpdir(), 'gha-'))
    const p = join(dir, 'gh-agent.json')
    writeFileSync(p, '{ not json')
    expect(loadGhAgentConfig(p)).toEqual(DEFAULTS)
    rmSync(dir, { recursive: true, force: true })
  })

  test('isAllowedAuthor is case-insensitive and exact', () => {
    const cfg = { ...DEFAULTS, allowlist: ['Alice'] }
    expect(isAllowedAuthor(cfg, 'alice')).toBe(true)
    expect(isAllowedAuthor(cfg, 'ALICE')).toBe(true)
    expect(isAllowedAuthor(cfg, 'mallory')).toBe(false)
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `vendor/bun/linux-x64/bun test src/gh-agent/config.test.ts`
Expected: FAIL — `Cannot find module './config.js'`.

- [ ] **Step 3: Write minimal implementation**

```ts
// src/cli/src/gh-agent/config.ts
import { readFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { join } from 'node:path'

export type GhAgentConfig = {
  repos: string[]
  allowlist: string[]
  trigger: string
  pollSeconds: number
  maxTasksPerHour: number
  model?: string
}

export const GH_AGENT_DIR = join(homedir(), '.jarvis', 'gh-agent')
export const CONFIG_PATH = join(homedir(), '.jarvis', 'gh-agent.json')

export const DEFAULTS: GhAgentConfig = {
  repos: [],
  allowlist: ['ulrichando'],
  trigger: '@jarvis',
  pollSeconds: 45,
  maxTasksPerHour: 6,
}

export function loadGhAgentConfig(path: string = CONFIG_PATH): GhAgentConfig {
  try {
    const raw = JSON.parse(readFileSync(path, 'utf8')) as Partial<GhAgentConfig>
    return {
      repos: Array.isArray(raw.repos) ? raw.repos : DEFAULTS.repos,
      allowlist: Array.isArray(raw.allowlist) ? raw.allowlist : DEFAULTS.allowlist,
      trigger: typeof raw.trigger === 'string' ? raw.trigger : DEFAULTS.trigger,
      pollSeconds: typeof raw.pollSeconds === 'number' ? raw.pollSeconds : DEFAULTS.pollSeconds,
      maxTasksPerHour: typeof raw.maxTasksPerHour === 'number' ? raw.maxTasksPerHour : DEFAULTS.maxTasksPerHour,
      model: typeof raw.model === 'string' ? raw.model : undefined,
    }
  } catch {
    return { ...DEFAULTS }
  }
}

export function isAllowedAuthor(cfg: GhAgentConfig, login: string): boolean {
  const l = login.toLowerCase()
  return cfg.allowlist.some(a => a.toLowerCase() === l)
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `vendor/bun/linux-x64/bun test src/gh-agent/config.test.ts`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/cli/src/gh-agent/config.ts src/cli/src/gh-agent/config.test.ts
git commit -m "feat(cli): gh-agent config loader + author allowlist gate" -- src/cli/src/gh-agent/config.ts src/cli/src/gh-agent/config.test.ts
```

---

## Task 2: cursor — per-repo since-marker

**Files:**
- Create: `src/cli/src/gh-agent/cursor.ts`
- Test: `src/cli/src/gh-agent/cursor.test.ts`

- [ ] **Step 1: Write the failing test**

```ts
// src/cli/src/gh-agent/cursor.test.ts
import { describe, expect, test } from 'bun:test'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { readCursor, advanceCursor } from './cursor.js'

describe('gh-agent cursor', () => {
  test('missing cursor → returns a valid ISO in the past', () => {
    const dir = mkdtempSync(join(tmpdir(), 'ghc-'))
    const iso = readCursor('owner/repo', dir)
    expect(new Date(iso).getTime()).toBeLessThanOrEqual(Date.now())
    rmSync(dir, { recursive: true, force: true })
  })

  test('advance then read returns the advanced value', () => {
    const dir = mkdtempSync(join(tmpdir(), 'ghc-'))
    advanceCursor('owner/repo', '2026-07-02T00:00:00Z', dir)
    expect(readCursor('owner/repo', dir)).toBe('2026-07-02T00:00:00Z')
    rmSync(dir, { recursive: true, force: true })
  })

  test('cursors are per-repo (no cross-contamination)', () => {
    const dir = mkdtempSync(join(tmpdir(), 'ghc-'))
    advanceCursor('owner/a', '2026-01-01T00:00:00Z', dir)
    advanceCursor('owner/b', '2026-02-02T00:00:00Z', dir)
    expect(readCursor('owner/a', dir)).toBe('2026-01-01T00:00:00Z')
    expect(readCursor('owner/b', dir)).toBe('2026-02-02T00:00:00Z')
    rmSync(dir, { recursive: true, force: true })
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `vendor/bun/linux-x64/bun test src/gh-agent/cursor.test.ts`
Expected: FAIL — `Cannot find module './cursor.js'`.

- [ ] **Step 3: Write minimal implementation**

```ts
// src/cli/src/gh-agent/cursor.ts
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { GH_AGENT_DIR } from './config.js'

// owner/name → owner__name.cursor (filesystem-safe, unambiguous: '/' is the
// only reserved char in a GitHub owner/name and becomes '__').
function cursorPath(repo: string, dir: string): string {
  return join(dir, `${repo.replace(/\//g, '__')}.cursor`)
}

export function readCursor(repo: string, dir: string = GH_AGENT_DIR): string {
  try {
    const v = readFileSync(cursorPath(repo, dir), 'utf8').trim()
    if (v && !Number.isNaN(new Date(v).getTime())) return v
  } catch {
    /* fall through to default */
  }
  // First run: look back 1h so we don't replay the entire repo history, but do
  // catch a mention posted moments before the agent first started.
  return new Date(Date.now() - 60 * 60 * 1000).toISOString()
}

export function advanceCursor(repo: string, iso: string, dir: string = GH_AGENT_DIR): void {
  mkdirSync(dir, { recursive: true })
  writeFileSync(cursorPath(repo, dir), iso)
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `vendor/bun/linux-x64/bun test src/gh-agent/cursor.test.ts`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/cli/src/gh-agent/cursor.ts src/cli/src/gh-agent/cursor.test.ts
git commit -m "feat(cli): gh-agent per-repo cursor (no-replay marker)" -- src/cli/src/gh-agent/cursor.ts src/cli/src/gh-agent/cursor.test.ts
```

---

## Task 3: gh — injectable wrappers (listMentions, postComment)

**Files:**
- Create: `src/cli/src/gh-agent/gh.ts`
- Test: `src/cli/src/gh-agent/gh.test.ts`

- [ ] **Step 1: Write the failing test**

```ts
// src/cli/src/gh-agent/gh.test.ts
import { describe, expect, test } from 'bun:test'
import { listMentions, postComment, type GhRunner } from './gh.js'

function stub(stdout: string): { run: GhRunner; calls: string[][] } {
  const calls: string[][] = []
  const run: GhRunner = async (args) => {
    calls.push(args)
    return { stdout, stderr: '', code: 0 }
  }
  return { run, calls }
}

describe('gh-agent gh wrappers', () => {
  test('listMentions parses comments and keeps only trigger matches', async () => {
    const api = JSON.stringify([
      { id: 1, body: 'hello', user: { login: 'bob' }, created_at: '2026-07-01T10:00:00Z', issue_url: 'https://api.github.com/repos/o/r/issues/12', html_url: 'https://github.com/o/r/issues/12#c1' },
      { id: 2, body: '@jarvis add tests', user: { login: 'alice' }, created_at: '2026-07-01T11:00:00Z', issue_url: 'https://api.github.com/repos/o/r/issues/13', html_url: 'https://github.com/o/r/issues/13#c2' },
    ])
    const { run } = stub(api)
    const mentions = await listMentions('o/r', '@jarvis', '2026-07-01T00:00:00Z', run)
    expect(mentions).toHaveLength(1)
    expect(mentions[0]).toMatchObject({ id: 2, author: 'alice', issueNumber: 13 })
    expect(mentions[0].body).toContain('@jarvis')
  })

  test('listMentions passes the since cursor to gh api', async () => {
    const { run, calls } = stub('[]')
    await listMentions('o/r', '@jarvis', '2026-07-01T00:00:00Z', run)
    const joined = calls[0].join(' ')
    expect(joined).toContain('repos/o/r/issues/comments')
    expect(joined).toContain('since=2026-07-01T00:00:00Z')
  })

  test('listMentions returns [] on nonzero gh exit (never throws)', async () => {
    const run: GhRunner = async () => ({ stdout: '', stderr: 'boom', code: 1 })
    expect(await listMentions('o/r', '@jarvis', '2026-07-01T00:00:00Z', run)).toEqual([])
  })

  test('postComment posts to the issue comments endpoint with the body', async () => {
    const { run, calls } = stub('{}')
    await postComment('o/r', 13, 'ack', run)
    const args = calls[0]
    expect(args.join(' ')).toContain('repos/o/r/issues/13/comments')
    expect(args).toContain('body=ack')
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `vendor/bun/linux-x64/bun test src/gh-agent/gh.test.ts`
Expected: FAIL — `Cannot find module './gh.js'`.

- [ ] **Step 3: Write minimal implementation**

```ts
// src/cli/src/gh-agent/gh.ts
import { execFileNoThrow } from '../utils/execFileNoThrow.js'

export type GhRunner = (args: string[]) => Promise<{ stdout: string; stderr: string; code: number }>

const defaultRunner: GhRunner = async (args) => {
  const r = await execFileNoThrow('gh', args)
  return { stdout: r.stdout, stderr: r.stderr, code: r.code }
}

export type Mention = {
  id: number
  body: string
  author: string
  createdAt: string
  issueNumber: number
  url: string
}

type RawComment = {
  id: number
  body: string
  user?: { login?: string }
  created_at: string
  issue_url: string
  html_url: string
}

function issueNumberFromUrl(issueUrl: string): number {
  const m = issueUrl.match(/\/issues\/(\d+)(?:$|[/?#])/)
  return m ? Number(m[1]) : 0
}

export async function listMentions(
  repo: string,
  trigger: string,
  sinceIso: string,
  run: GhRunner = defaultRunner,
): Promise<Mention[]> {
  const r = await run([
    'api',
    `repos/${repo}/issues/comments?since=${sinceIso}&per_page=100&sort=created&direction=asc`,
    '--paginate',
  ])
  if (r.code !== 0) return []
  let raw: RawComment[]
  try {
    raw = JSON.parse(r.stdout) as RawComment[]
  } catch {
    return []
  }
  if (!Array.isArray(raw)) return []
  return raw
    .filter(c => typeof c.body === 'string' && c.body.includes(trigger))
    .map(c => ({
      id: c.id,
      body: c.body,
      author: c.user?.login ?? '',
      createdAt: c.created_at,
      issueNumber: issueNumberFromUrl(c.issue_url),
      url: c.html_url,
    }))
    .filter(m => m.issueNumber > 0 && m.author !== '')
}

export async function postComment(
  repo: string,
  issueNumber: number,
  body: string,
  run: GhRunner = defaultRunner,
): Promise<boolean> {
  const r = await run([
    'api',
    '-X',
    'POST',
    `repos/${repo}/issues/${issueNumber}/comments`,
    '-f',
    `body=${body}`,
  ])
  return r.code === 0
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `vendor/bun/linux-x64/bun test src/gh-agent/gh.test.ts`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/cli/src/gh-agent/gh.ts src/cli/src/gh-agent/gh.test.ts
git commit -m "feat(cli): gh-agent gh wrappers (listMentions, postComment)" -- src/cli/src/gh-agent/gh.ts src/cli/src/gh-agent/gh.test.ts
```

---

## Task 4: main — one-sweep loop (`runGhAgentOnce`)

**Files:**
- Create: `src/cli/src/gh-agent/main.ts`
- Test: `src/cli/src/gh-agent/main.test.ts`

- [ ] **Step 1: Write the failing test**

```ts
// src/cli/src/gh-agent/main.test.ts
import { describe, expect, test } from 'bun:test'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { runGhAgentOnce } from './main.js'
import type { GhRunner } from './gh.js'
import { DEFAULTS } from './config.js'

const comments = JSON.stringify([
  { id: 2, body: '@jarvis do X', user: { login: 'ulrichando' }, created_at: '2026-07-01T11:00:00Z', issue_url: 'https://api.github.com/repos/o/r/issues/13', html_url: 'u13' },
  { id: 3, body: '@jarvis do Y', user: { login: 'mallory' }, created_at: '2026-07-01T12:00:00Z', issue_url: 'https://api.github.com/repos/o/r/issues/14', html_url: 'u14' },
])

function recorder(stdout: string) {
  const posts: string[][] = []
  const run: GhRunner = async (args) => {
    if (args[1] === '-X' || args.includes('POST')) { posts.push(args); return { stdout: '{}', stderr: '', code: 0 } }
    return { stdout, stderr: '', code: 0 }
  }
  return { run, posts }
}

describe('gh-agent runGhAgentOnce', () => {
  test('posts an ack ONLY for the allowlisted author, skips others', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'ghm-'))
    const { run, posts } = recorder(comments)
    await runGhAgentOnce({ repo: 'o/r', dryRun: false }, { run, cfg: { ...DEFAULTS, allowlist: ['ulrichando'] }, cursorDir: dir })
    expect(posts).toHaveLength(1)
    expect(posts[0].join(' ')).toContain('repos/o/r/issues/13/comments')
    rmSync(dir, { recursive: true, force: true })
  })

  test('dry-run posts nothing', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'ghm-'))
    const { run, posts } = recorder(comments)
    await runGhAgentOnce({ repo: 'o/r', dryRun: true }, { run, cfg: { ...DEFAULTS, allowlist: ['ulrichando'] }, cursorDir: dir })
    expect(posts).toHaveLength(0)
    rmSync(dir, { recursive: true, force: true })
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `vendor/bun/linux-x64/bun test src/gh-agent/main.test.ts`
Expected: FAIL — `Cannot find module './main.js'`.

- [ ] **Step 3: Write minimal implementation**

```ts
// src/cli/src/gh-agent/main.ts
import { type GhAgentConfig, isAllowedAuthor, loadGhAgentConfig } from './config.js'
import { advanceCursor, readCursor } from './cursor.js'
import { type GhRunner, listMentions, postComment, type Mention } from './gh.js'

export type RunOnceArgs = { repo?: string; dryRun: boolean }
export type RunOnceDeps = { run?: GhRunner; cfg?: GhAgentConfig; cursorDir?: string }

function log(msg: string): void {
  process.stdout.write(`[gh-agent] ${msg}\n`)
}

function taskText(body: string, trigger: string): string {
  const i = body.indexOf(trigger)
  return (i === -1 ? body : body.slice(i + trigger.length)).trim()
}

export async function runGhAgentOnce(args: RunOnceArgs, deps: RunOnceDeps = {}): Promise<void> {
  const cfg = deps.cfg ?? loadGhAgentConfig()
  const repos = args.repo ? [args.repo] : cfg.repos
  if (repos.length === 0) {
    log('no repos configured (set repos[] in ~/.jarvis/gh-agent.json or pass --repo owner/name)')
    return
  }
  for (const repo of repos) {
    const since = readCursor(repo, deps.cursorDir)
    const mentions: Mention[] = await listMentions(repo, cfg.trigger, since, deps.run)
    log(`${repo}: ${mentions.length} new mention(s) since ${since}`)
    // Oldest-first so the cursor advances monotonically.
    const ordered = [...mentions].sort((a, b) => a.createdAt.localeCompare(b.createdAt))
    for (const m of ordered) {
      if (!isAllowedAuthor(cfg, m.author)) {
        log(`  #${m.issueNumber} ignored — @${m.author} not in allowlist`)
        advanceCursor(repo, m.createdAt, deps.cursorDir)
        continue
      }
      const task = taskText(m.body, cfg.trigger)
      if (args.dryRun) {
        log(`  #${m.issueNumber} DRY-RUN would ack @${m.author}: "${task}"`)
      } else {
        const ok = await postComment(
          repo,
          m.issueNumber,
          `👀 Jarvis received this from @${m.author}: "${task}"\n\n_(P1: acknowledgement only — automated execution lands in P2.)_`,
          deps.run,
        )
        log(`  #${m.issueNumber} ${ok ? 'acked' : 'ACK FAILED'} @${m.author}`)
      }
      advanceCursor(repo, m.createdAt, deps.cursorDir)
    }
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `vendor/bun/linux-x64/bun test src/gh-agent/main.test.ts`
Expected: PASS (2 tests).

- [ ] **Step 5: Run the whole gh-agent suite**

Run: `vendor/bun/linux-x64/bun test src/gh-agent/`
Expected: PASS (all 13 tests across the 4 files).

- [ ] **Step 6: Commit**

```bash
git add src/cli/src/gh-agent/main.ts src/cli/src/gh-agent/main.test.ts
git commit -m "feat(cli): gh-agent one-sweep loop (poll, gate, ack, cursor)" -- src/cli/src/gh-agent/main.ts src/cli/src/gh-agent/main.test.ts
```

---

## Task 5: register the `jarvis gh-agent` command

**Files:**
- Modify: `src/cli/src/main.tsx` (add a top-level command near the `keys` command registration, ~line 5962)

- [ ] **Step 1: Add the command registration**

Find the `keys` command block (search for `const keysCmd = program`). Immediately AFTER its `keysCmd.command('pull')…` block and before the next `program.command(...)`, insert:

```ts
  // jarvis gh-agent — poll a GitHub repo for @jarvis mentions by allowlisted
  // authors and (P1) acknowledge them. Reuses the machine's authed gh CLI.
  // Config: ~/.jarvis/gh-agent.json. See docs/superpowers/specs/2026-07-02-jarvis-gh-agent-design.md
  program
    .command("gh-agent")
    .description("Watch a GitHub repo for @jarvis mentions (P1: acknowledge)")
    .option("--repo <owner/name>", "Repo to poll (overrides config repos[])")
    .option("--once", "Do a single poll sweep and exit (default)")
    .option("--dry-run", "Log what would happen; post nothing")
    .action(async (opts: { repo?: string; once?: boolean; dryRun?: boolean }) => {
      const { runGhAgentOnce } = await import("./gh-agent/main.js");
      await runGhAgentOnce({ repo: opts.repo, dryRun: !!opts.dryRun });
      process.exit(0);
    });
```

- [ ] **Step 2: Verify the file parses**

Run (from `src/cli/`): `vendor/bun/linux-x64/bun build src/main.tsx --no-bundle`
Expected: no errors (exit 0).

- [ ] **Step 3: Verify the command is wired (help)**

Run (from repo root): `bin/jarvis gh-agent --help`
Expected: shows the description + `--repo`, `--once`, `--dry-run` options (no crash, no fall-through to the agent).

- [ ] **Step 4: Live dry-run smoke**

Set up config, then dry-run against the real repo (safe — posts nothing):

```bash
mkdir -p ~/.jarvis/gh-agent
printf '{"repos":["ulrichando/jarvis"],"allowlist":["ulrichando"]}' > ~/.jarvis/gh-agent.json
bin/jarvis gh-agent --repo ulrichando/jarvis --dry-run
```

Expected: `[gh-agent] ulrichando/jarvis: N new mention(s) since <iso>` and, if any `@jarvis` comments exist, `DRY-RUN would ack …`. No comment is posted. (N is usually 0 on a repo with no recent `@jarvis` comments — that's a valid pass.)

- [ ] **Step 5: End-to-end ack test (real comment, throwaway issue)**

```bash
# create a throwaway issue and an @jarvis comment authored by ulrichando
num=$(gh issue create --repo ulrichando/jarvis --title "gh-agent P1 smoke" --body "test" --json number --jq .number 2>/dev/null || gh issue create --repo ulrichando/jarvis --title "gh-agent P1 smoke" --body "test" | grep -oE '/issues/[0-9]+' | grep -oE '[0-9]+')
gh issue comment "$num" --repo ulrichando/jarvis --body "@jarvis say hello"
# reset cursor so the sweep sees it, then run for real
rm -f ~/.jarvis/gh-agent/ulrichando__jarvis.cursor
bin/jarvis gh-agent --repo ulrichando/jarvis
# verify the agent replied, then close
gh issue view "$num" --repo ulrichando/jarvis --comments | grep -c "Jarvis received this"
gh issue close "$num" --repo ulrichando/jarvis
```

Expected: the `grep -c` prints `1` (the agent posted its acknowledgement). Then the issue is closed.

- [ ] **Step 6: Commit**

```bash
git add src/cli/src/main.tsx
git commit -m "feat(cli): register jarvis gh-agent command (P1 poll+ack)" -- src/cli/src/main.tsx
```

---

## Self-Review (completed)

- **Spec coverage (P1 slice):** config+allowlist (Task 1), cursor/no-replay (Task 2), gh poll+comment reusing authed gh (Task 3), one-sweep loop with allowlist gate + dry-run (Task 4), `jarvis gh-agent` command (Task 5). P2 (worktree + `jarvis -p` + branch push + PR comment) and P3 (`--watch` daemon + systemd + rate cap) are deferred to their own plans, per the spec's phasing.
- **Placeholders:** none — every step has full code/commands + expected output.
- **Type consistency:** `GhAgentConfig`, `GhRunner`, `Mention`, `runGhAgentOnce(args, deps)`, `readCursor/advanceCursor(repo, [iso], dir)`, `listMentions(repo, trigger, sinceIso, run)`, `postComment(repo, issueNumber, body, run)` used consistently across tasks.
- **Safety:** author allowlist gate is enforced before any post (Task 4); dry-run posts nothing; no code execution or pushes in P1.

## Next plans (not this one)

- **P2** — `task.ts`: throwaway git worktree → `jarvis -p "<task>"` → push branch → `gh pr comment`; wire into `runGhAgentOnce` (replace ack with execution), keep dry-run.
- **P3** — `--watch` daemon loop (`pollSeconds`), `maxTasksPerHour` cap, 👀-reaction claim for idempotency, optional `jarvis-gh-agent.service` systemd `--user` unit.
