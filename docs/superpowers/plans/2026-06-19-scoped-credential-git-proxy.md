# Scoped-credential git proxy — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep the user's real GitHub PAT out of every `/code` container; in-container git authenticates to a host-side proxy with a per-session capability token, and the proxy enforces repo+operation scope before injecting the real token upstream.

**Architecture:** A new Next.js App-Router bridge route (`/api/bridge/v1/code/sessions/[sessionId]/git/[...path]`) transparently reverse-proxies GitHub smart-HTTP to `github.com`, reading `owner/repo` + operation from the URL (no packfile parsing) and attaching `Authorization: Basic x-access-token:<PAT>` host-side. Container launch is rewired to clone/push through that route with a per-session cap token; `GH_TOKEN`/`GITHUB_TOKEN` and the real-token clone URL are removed; PR/merge move to host-side REST.

**Tech Stack:** TypeScript, Next.js App Router (route handlers), Node `fetch`/undici streaming, better-sqlite3 store, vitest.

**Working dir for ALL commands:** `src/web` (`cd /home/ulrich/Documents/Projects/jarvis/src/web`).

---

## File structure

| File | Responsibility |
|---|---|
| `src/lib/bridge/git-proxy.ts` (new) | Pure policy + forwarding: `parseGitRequest`, `assertRepoAllowed`, `forwardToGithub`. No DB, no Next. |
| `src/app/api/bridge/v1/code/sessions/[sessionId]/git/[...path]/route.ts` (new) | Thin GET/POST handler: Basic-auth → cap token → validate → scope-check → forward. |
| `src/lib/bridge/store.ts` (modify) | Persist cap token + repo scope inside `container_json`; `validateGitCapToken`, `getSessionGitScope`; widen `setSessionContainer`. |
| `src/lib/bridge/containers.ts` (modify) | `configureGitProxy` (cap token, proxy remote, no PAT); clone via proxy URL; drop `GH_TOKEN`/`GITHUB_TOKEN`; persist scope; drop `github.com` from squid; PR/merge host-side. |
| `src/lib/connectors/github.ts` (modify) | Host-side REST `openPullRequest`, `mergePullRequest`. |
| `tests/bridge/git-proxy.test.ts` (new) | Policy + route unit tests (fetch mocked). |
| `tests/bridge/containers-git-proxy.test.ts` (new) | Launch leaks no real token; clones via proxy URL; persists scope. |

---

## Task 1: Git-proxy policy — `parseGitRequest` + `assertRepoAllowed`

**Files:**
- Create: `src/lib/bridge/git-proxy.ts`
- Test: `tests/bridge/git-proxy.test.ts`

- [ ] **Step 1: Write the failing test**

```ts
// tests/bridge/git-proxy.test.ts
import { describe, expect, test } from 'vitest'
import { parseGitRequest, assertRepoAllowed } from '@/lib/bridge/git-proxy'

describe('parseGitRequest', () => {
  test('info/refs fetch (v1+v2 share the path)', () => {
    const r = parseGitRequest(['owner', 'demo.git', 'info', 'refs'], new URLSearchParams('service=git-upload-pack'))
    expect(r).toEqual({ owner: 'owner', repo: 'demo', service: 'git-upload-pack', kind: 'info-refs' })
  })
  test('info/refs push', () => {
    const r = parseGitRequest(['owner', 'demo.git', 'info', 'refs'], new URLSearchParams('service=git-receive-pack'))
    expect(r?.service).toBe('git-receive-pack')
    expect(r?.kind).toBe('info-refs')
  })
  test('service POST endpoints', () => {
    expect(parseGitRequest(['o', 'r.git', 'git-upload-pack'], new URLSearchParams())?.kind).toBe('service')
    expect(parseGitRequest(['o', 'r', 'git-receive-pack'], new URLSearchParams())?.service).toBe('git-receive-pack')
  })
  test('rejects junk / unknown service / traversal', () => {
    expect(parseGitRequest(['owner', 'demo.git', 'info', 'refs'], new URLSearchParams('service=evil'))).toBeNull()
    expect(parseGitRequest(['owner', 'demo.git', 'HEAD'], new URLSearchParams())).toBeNull()
    expect(parseGitRequest(['..', 'demo.git', 'git-upload-pack'], new URLSearchParams())).toBeNull()
    expect(parseGitRequest(['owner'], new URLSearchParams())).toBeNull()
  })
})

describe('assertRepoAllowed', () => {
  test('membership is case-insensitive and .git-insensitive', () => {
    expect(assertRepoAllowed(['Owner/Demo'], 'owner', 'demo')).toBe(true)
    expect(assertRepoAllowed(['owner/demo.git'], 'owner', 'demo')).toBe(true)
    expect(assertRepoAllowed(['owner/demo'], 'owner', 'other')).toBe(false)
    expect(assertRepoAllowed([], 'owner', 'demo')).toBe(false)
  })
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `npx vitest run tests/bridge/git-proxy.test.ts`
Expected: FAIL — `Cannot find module '@/lib/bridge/git-proxy'`.

- [ ] **Step 3: Implement the policy functions**

```ts
// src/lib/bridge/git-proxy.ts
import 'server-only'

export const GITHUB_GIT_BASE = 'https://github.com'

export type GitService = 'git-upload-pack' | 'git-receive-pack'

export interface GitRequest {
  owner: string
  repo: string // no trailing .git
  service: GitService
  kind: 'info-refs' | 'service'
}

const NAME = /^[A-Za-z0-9_.-]+$/

/**
 * Parse the App-Router catch-all segments (everything after `.../git/`) into a
 * recognized smart-HTTP request, or null. Two shapes only:
 *   [owner, repo(.git), 'info', 'refs'] + ?service=git-(upload|receive)-pack
 *   [owner, repo(.git), 'git-(upload|receive)-pack']
 * Anything else (HEAD, objects/*, traversal) → null (rejected, never forwarded).
 */
export function parseGitRequest(segments: string[], search: URLSearchParams): GitRequest | null {
  if (segments.length < 3) return null
  const owner = segments[0]
  const repo = (segments[1] ?? '').replace(/\.git$/, '')
  if (!NAME.test(owner) || !NAME.test(repo)) return null
  const tail = segments.slice(2)
  if (tail.length === 2 && tail[0] === 'info' && tail[1] === 'refs') {
    const svc = search.get('service')
    if (svc === 'git-upload-pack' || svc === 'git-receive-pack') {
      return { owner, repo, service: svc, kind: 'info-refs' }
    }
    return null
  }
  if (tail.length === 1 && (tail[0] === 'git-upload-pack' || tail[0] === 'git-receive-pack')) {
    return { owner, repo, service: tail[0], kind: 'service' }
  }
  return null
}

/** Case- and `.git`-insensitive membership of `owner/repo` in the allowed set. */
export function assertRepoAllowed(allowedRepos: string[], owner: string, repo: string): boolean {
  const want = `${owner}/${repo}`.toLowerCase()
  return allowedRepos.some((r) => r.replace(/\.git$/, '').toLowerCase() === want)
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `npx vitest run tests/bridge/git-proxy.test.ts`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/lib/bridge/git-proxy.ts tests/bridge/git-proxy.test.ts
git commit -m "feat(code): git-proxy policy — parse smart-HTTP path + repo allowlist"
```

---

## Task 2: Git-proxy forwarding — `forwardToGithub`

**Files:**
- Modify: `src/lib/bridge/git-proxy.ts`
- Test: `tests/bridge/git-proxy.test.ts`

- [ ] **Step 1: Write the failing test** (append to the file)

```ts
import { afterEach, beforeEach, vi } from 'vitest'
import { forwardToGithub, type GitRequest } from '@/lib/bridge/git-proxy'

describe('forwardToGithub', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('PACK', {
      status: 200,
      headers: { 'content-type': 'application/x-git-upload-pack-result' },
    })))
  })
  afterEach(() => vi.unstubAllGlobals())

  test('injects Basic x-access-token PAT, forwards git headers, builds upstream URL', async () => {
    const target: GitRequest = { owner: 'owner', repo: 'demo', service: 'git-upload-pack', kind: 'info-refs' }
    const req = new Request('http://host/whatever', {
      method: 'GET',
      headers: { 'git-protocol': 'version=2', accept: 'application/x-git-upload-pack-advertisement' },
    })
    const res = await forwardToGithub(req, target, 'PAT123')
    expect(res.status).toBe(200)
    expect(res.headers.get('content-type')).toBe('application/x-git-upload-pack-result')
    const [url, init] = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(String(url)).toBe('https://github.com/owner/demo.git/info/refs?service=git-upload-pack')
    const h = init.headers as Headers
    expect(h.get('authorization')).toBe('Basic ' + Buffer.from('x-access-token:PAT123').toString('base64'))
    expect(h.get('git-protocol')).toBe('version=2')
    expect(h.get('accept')).toBe('application/x-git-upload-pack-advertisement')
  })

  test('service POST targets the named endpoint and streams the body', async () => {
    const target: GitRequest = { owner: 'o', repo: 'r', service: 'git-receive-pack', kind: 'service' }
    const req = new Request('http://host/x', { method: 'POST', body: 'PUSHDATA', headers: { 'content-type': 'application/x-git-receive-pack-request' } })
    await forwardToGithub(req, target, 'P')
    const [url, init] = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(String(url)).toBe('https://github.com/o/r.git/git-receive-pack')
    expect(init.method).toBe('POST')
    expect(init.duplex).toBe('half')
  })
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `npx vitest run tests/bridge/git-proxy.test.ts -t forwardToGithub`
Expected: FAIL — `forwardToGithub is not a function`.

- [ ] **Step 3: Implement `forwardToGithub`** (append to `git-proxy.ts`)

```ts
/**
 * Transparently reverse-proxy ONE git smart-HTTP request to github.com with the
 * real PAT injected as Basic auth. Forwards only the headers git needs
 * (content-type, accept, git-protocol, user-agent) — deliberately NOT
 * accept-encoding, so undici decodes upstream and we pass plain bytes (avoids
 * the gzip double-decode proxy trap). Streams request + response bodies.
 */
export async function forwardToGithub(req: Request, target: GitRequest, pat: string): Promise<Response> {
  const path = target.kind === 'info-refs' ? 'info/refs' : target.service
  const url = new URL(`${GITHUB_GIT_BASE}/${target.owner}/${target.repo}.git/${path}`)
  if (target.kind === 'info-refs') url.searchParams.set('service', target.service)

  const headers = new Headers()
  headers.set('authorization', 'Basic ' + Buffer.from(`x-access-token:${pat}`).toString('base64'))
  for (const h of ['content-type', 'accept', 'git-protocol', 'user-agent']) {
    const v = req.headers.get(h)
    if (v) headers.set(h, v)
  }

  const method = req.method.toUpperCase()
  const hasBody = method !== 'GET' && method !== 'HEAD'
  const init: RequestInit & { duplex?: 'half' } = { method, headers, redirect: 'manual' }
  if (hasBody) {
    init.body = req.body
    init.duplex = 'half' // undici requires this when streaming a request body
  }
  const upstream = await fetch(url, init as RequestInit)

  const out = new Headers()
  for (const h of ['content-type', 'cache-control']) {
    const v = upstream.headers.get(h)
    if (v) out.set(h, v)
  }
  return new Response(upstream.body, { status: upstream.status, headers: out })
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `npx vitest run tests/bridge/git-proxy.test.ts`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add src/lib/bridge/git-proxy.ts tests/bridge/git-proxy.test.ts
git commit -m "feat(code): git-proxy forwarder — inject real PAT host-side, stream to github"
```

---

## Task 3: Store — cap token + repo scope (inside `container_json`)

**Files:**
- Modify: `src/lib/bridge/store.ts` (widen `setSessionContainer` ~`:771`; add helpers after it)
- Test: `tests/bridge/store.test.ts`

- [ ] **Step 1: Write the failing test** (append to `tests/bridge/store.test.ts`)

```ts
import { setSessionContainer, getSessionGitScope, validateGitCapToken, findSession } from '@/lib/bridge/store'

describe('git scope + cap token', () => {
  test('persists scope + cap token in container_json; validates', () => {
    const store = getStore()
    // a session must exist first (FK-free table, but mirror real usage)
    store.db.prepare('INSERT INTO sessions (session_id, environment_id, archived, created_at, worker_epoch) VALUES (?, NULL, 0, ?, 0)').run('s1', Date.now())
    setSessionContainer(store, 's1', { container: 'c', repo: 'Owner/Demo', extraRepos: ['o2/lib'], gitCapToken: 'git_abc' })
    const s = findSession(store, 's1')!
    expect(getSessionGitScope(s)).toEqual(['Owner/Demo', 'o2/lib'])
    expect(validateGitCapToken(store, 's1', 'git_abc')).toBe(true)
    expect(validateGitCapToken(store, 's1', 'nope')).toBe(false)
  })
  test('legacy container_json (no scope) → empty scope, no token', () => {
    const store = getStore()
    store.db.prepare('INSERT INTO sessions (session_id, environment_id, archived, created_at, worker_epoch) VALUES (?, NULL, 0, ?, 0)').run('s2', Date.now())
    setSessionContainer(store, 's2', { container: 'c', repo: 'o/legacy' })
    expect(getSessionGitScope(findSession(store, 's2')!)).toEqual(['o/legacy'])
    expect(validateGitCapToken(store, 's2', 'anything')).toBe(false)
  })
})
```

> Note: `tests/bridge/store.test.ts` already imports `getStore` + calls `_resetForTests()` in `beforeEach`. If those imports are absent, add `import { _resetForTests, getStore } from '@/lib/bridge/db'` and a `beforeEach(() => _resetForTests())`.

- [ ] **Step 2: Run to verify it fails**

Run: `npx vitest run tests/bridge/store.test.ts -t "git scope"`
Expected: FAIL — `getSessionGitScope is not exported`.

- [ ] **Step 3: Widen `setSessionContainer` + add helpers**

Replace the existing `setSessionContainer` (around `:771`) with the widened signature and add the two helpers + the private parser directly below it:

```ts
/** Persisted container meta — git scope + cap token live here (no schema change). */
interface ContainerMeta {
  container?: string
  repo?: string
  extraRepos?: string[]
  gitCapToken?: string
}

function parseContainerMeta(session: SessionRow | null): ContainerMeta {
  if (!session?.container_json) return {}
  try {
    return JSON.parse(session.container_json) as ContainerMeta
  } catch {
    return {}
  }
}

/** Record the docker container backing a session, plus its git proxy scope. */
export function setSessionContainer(
  store: Store,
  sessionId: string,
  meta: { container: string; repo: string; extraRepos?: string[]; gitCapToken?: string },
): void {
  store.db
    .prepare('UPDATE sessions SET container_json = ? WHERE session_id = ?')
    .run(JSON.stringify(meta), sessionId)
}

/** Repos a session's git proxy may touch: primary + extras (verbatim casing). */
export function getSessionGitScope(session: SessionRow): string[] {
  const m = parseContainerMeta(session)
  const out: string[] = []
  if (m.repo) out.push(m.repo)
  for (const r of m.extraRepos ?? []) if (r) out.push(r)
  return out
}

/** True when `token` matches the session's stored git capability token. */
export function validateGitCapToken(store: Store, sessionId: string, token: string): boolean {
  const m = parseContainerMeta(findSession(store, sessionId))
  return !!m.gitCapToken && m.gitCapToken === token
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `npx vitest run tests/bridge/store.test.ts -t "git scope"`
Expected: PASS (2 tests). Then `npx vitest run tests/bridge/store.test.ts` — all green (widening is back-compat).

- [ ] **Step 5: Commit**

```bash
git add src/lib/bridge/store.ts tests/bridge/store.test.ts
git commit -m "feat(code): persist per-session git scope + cap token in container_json"
```

---

## Task 4: The proxy route handler

**Files:**
- Create: `src/app/api/bridge/v1/code/sessions/[sessionId]/git/[...path]/route.ts`
- Test: `tests/bridge/git-proxy.test.ts` (append a `describe('route')`)

- [ ] **Step 1: Write the failing test** (append)

```ts
import { _resetForTests, getStore } from '@/lib/bridge/db'
import { setSessionContainer } from '@/lib/bridge/store'

function seedSession(scopeRepo = 'owner/demo', cap = 'git_cap') {
  const store = getStore()
  store.db.prepare('INSERT INTO sessions (session_id, environment_id, archived, created_at, worker_epoch) VALUES (?, NULL, 0, ?, 0)').run('sess1', Date.now())
  setSessionContainer(store, 'sess1', { container: 'c', repo: scopeRepo, gitCapToken: cap })
}
function basic(cap: string) {
  return 'Basic ' + Buffer.from(`x-access-token:${cap}`).toString('base64')
}
function ctx(path: string[]) {
  return { params: Promise.resolve({ sessionId: 'sess1', path }) }
}

describe('git proxy route', () => {
  beforeEach(() => {
    _resetForTests()
    vi.stubGlobal('fetch', vi.fn(async () => new Response('OK', { status: 200, headers: { 'content-type': 'application/x-git-upload-pack-advertisement' } })))
  })
  afterEach(() => vi.unstubAllGlobals())

  test('401 on bad cap token', async () => {
    seedSession()
    const { GET } = await import('@/app/api/bridge/v1/code/sessions/[sessionId]/git/[...path]/route')
    const req = new Request('http://h/info/refs?service=git-upload-pack', { headers: { authorization: basic('wrong') } })
    const res = await GET(req, ctx(['owner', 'demo.git', 'info', 'refs']))
    expect(res.status).toBe(401)
  })

  test('403 + audit event on out-of-scope repo', async () => {
    seedSession('owner/demo', 'git_cap')
    vi.doMock('@/lib/connectors/github', () => ({ getGithubToken: async () => 'PAT' }))
    const { GET } = await import('@/app/api/bridge/v1/code/sessions/[sessionId]/git/[...path]/route')
    const { listSessionEvents } = await import('@/lib/bridge/store')
    const req = new Request('http://h/info/refs?service=git-upload-pack', { headers: { authorization: basic('git_cap') } })
    const res = await GET(req, ctx(['evil', 'other.git', 'info', 'refs']))
    expect(res.status).toBe(403)
    const events = listSessionEvents(getStore(), 'sess1', 0)
    expect(events.some((e) => /blocked out-of-scope/.test(e.payload_json))).toBe(true)
    vi.doUnmock('@/lib/connectors/github')
  })

  test('happy path forwards + streams 200', async () => {
    seedSession('owner/demo', 'git_cap')
    vi.doMock('@/lib/connectors/github', () => ({ getGithubToken: async () => 'PAT' }))
    const { GET } = await import('@/app/api/bridge/v1/code/sessions/[sessionId]/git/[...path]/route')
    const req = new Request('http://h/info/refs?service=git-upload-pack', { headers: { authorization: basic('git_cap') } })
    const res = await GET(req, ctx(['owner', 'demo.git', 'info', 'refs']))
    expect(res.status).toBe(200)
    expect(res.headers.get('content-type')).toContain('x-git-upload-pack-advertisement')
    const [url, init] = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(String(url)).toContain('github.com/owner/demo.git/info/refs')
    expect((init.headers as Headers).get('authorization')).toContain('Basic ')
    vi.doUnmock('@/lib/connectors/github')
  })

  test('503 when no PAT host-side', async () => {
    seedSession('owner/demo', 'git_cap')
    vi.doMock('@/lib/connectors/github', () => ({ getGithubToken: async () => null }))
    const { GET } = await import('@/app/api/bridge/v1/code/sessions/[sessionId]/git/[...path]/route')
    const req = new Request('http://h/info/refs?service=git-upload-pack', { headers: { authorization: basic('git_cap') } })
    const res = await GET(req, ctx(['owner', 'demo.git', 'info', 'refs']))
    expect(res.status).toBe(503)
    vi.doUnmock('@/lib/connectors/github')
  })
})
```

> `listSessionEvents(store, sessionId, sinceRowid)` already exists (`store.ts:1463`). If its signature differs, read it and adjust the call.

- [ ] **Step 2: Run to verify it fails**

Run: `npx vitest run tests/bridge/git-proxy.test.ts -t "git proxy route"`
Expected: FAIL — cannot import the route module.

- [ ] **Step 3: Implement the route**

```ts
// src/app/api/bridge/v1/code/sessions/[sessionId]/git/[...path]/route.ts
import { getStore } from '@/lib/bridge/db'
import { findSession, validateGitCapToken, getSessionGitScope, appendSessionEvent } from '@/lib/bridge/store'
import { getGithubToken } from '@/lib/connectors/github'
import { parseGitRequest, assertRepoAllowed, forwardToGithub } from '@/lib/bridge/git-proxy'
import { bridgeError } from '@/lib/bridge/errors'

type Ctx = { params: Promise<{ sessionId: string; path: string[] }> }

/** The cap token is the Basic-auth password git sends (username ignored). */
function capTokenFrom(req: Request): string | null {
  const h = req.headers.get('authorization')
  const m = h && /^Basic\s+(.+)$/i.exec(h)
  if (!m) return null
  try {
    const dec = Buffer.from(m[1], 'base64').toString('utf8')
    const i = dec.indexOf(':')
    return i >= 0 ? dec.slice(i + 1) : dec
  } catch {
    return null
  }
}

async function handle(req: Request, ctx: Ctx): Promise<Response> {
  const { sessionId, path } = await ctx.params
  const store = getStore()
  const cap = capTokenFrom(req)
  if (!cap || !validateGitCapToken(store, sessionId, cap)) {
    return bridgeError(401, 'unauthorized', 'Invalid git credential')
  }
  const target = parseGitRequest(path, new URL(req.url).searchParams)
  if (!target) return bridgeError(400, 'invalid_request', 'Unrecognized git path')
  const scope = getSessionGitScope(findSession(store, sessionId)!)
  if (!assertRepoAllowed(scope, target.owner, target.repo)) {
    appendSessionEvent(store, sessionId, {
      type: 'status',
      payload: { type: 'status', status: `⚠ git proxy blocked out-of-scope repo ${target.owner}/${target.repo}` },
    })
    return bridgeError(403, 'forbidden', 'Repository not in session scope')
  }
  const pat = await getGithubToken()
  if (!pat) return bridgeError(503, 'github_unavailable', 'GitHub not connected — reconnect in Settings')
  if (target.kind === 'service') {
    // Audit the data op (not the chatty info/refs probe) to the server log.
    console.info(`[git-proxy] ${sessionId} ${target.owner}/${target.repo} ${target.service}`)
  }
  return forwardToGithub(req, target, pat)
}

export async function GET(req: Request, ctx: Ctx): Promise<Response> {
  return handle(req, ctx)
}
export async function POST(req: Request, ctx: Ctx): Promise<Response> {
  return handle(req, ctx)
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `npx vitest run tests/bridge/git-proxy.test.ts`
Expected: PASS (all, incl. the 4 route tests).

- [ ] **Step 5: Commit**

```bash
git add "src/app/api/bridge/v1/code/sessions/[sessionId]/git/[...path]/route.ts" tests/bridge/git-proxy.test.ts
git commit -m "feat(code): git proxy route — cap-token auth, repo-scope gate, audit"
```

---

## Task 5: Rewire container launch — proxy URL in, real token out

**Files:**
- Modify: `src/lib/bridge/containers.ts` (`DEFAULT_ALLOW` `:53`; `configureGitCreds`→`configureGitProxy` `:353`; clone `:374-431`; `setSessionContainer` `:335`; `childEnv` `:628`)
- Test: `tests/bridge/containers-git-proxy.test.ts` (new)

- [ ] **Step 1: Write the failing test**

```ts
// tests/bridge/containers-git-proxy.test.ts
import { describe, expect, test, beforeEach, afterEach, vi } from 'vitest'
import { _resetForTests, getStore } from '@/lib/bridge/db'
import { getOrCreateSession, createEnvironment, findSession, getSessionGitScope } from '@/lib/bridge/store'
import { launchContainerSession, type DockerExec } from '@/lib/bridge/containers'

vi.mock('@/lib/auth-helpers', () => ({ getUserId: async () => '00000000-0000-0000-0000-000000000001' }))
vi.mock('@/lib/connectors/github', () => ({
  getGithubToken: async () => 'ghp_REAL_SECRET',
  githubStatus: async () => ({ connected: true, login: 'tester' }),
}))
vi.mock('@/lib/mcp/store', () => ({ listMcpServers: vi.fn(async () => []) }))

function fakeDocker() {
  const calls: string[][] = []
  const exec: DockerExec = async (args) => {
    calls.push(args)
    if (args.some((a) => a.includes('test -f'))) return { stdout: 'no\n', stderr: '' }
    return { stdout: '', stderr: '' }
  }
  return { calls, exec }
}
function makeSession(): string {
  const store = getStore()
  const env = createEnvironment(store, { machine_name: 'Cloud container', directory: '/workspace', git_repo_url: 'https://github.com/owner/demo', max_sessions: 4, worker_type: 'container', user_id: '00000000-0000-0000-0000-000000000001' })
  getOrCreateSession(store, 'c0ffee0011223344', env.environment_id)
  return 'c0ffee0011223344'
}

beforeEach(() => {
  _resetForTests()
  vi.stubGlobal('fetch', vi.fn(async () => { throw new Error('no proxy in tests') }))
})
afterEach(() => vi.unstubAllGlobals())

describe('launch keeps the real PAT out of the container', () => {
  test('clone uses the proxy URL, no GH_TOKEN, scope persisted', async () => {
    const sid = makeSession()
    const { calls, exec } = fakeDocker()
    await launchContainerSession(getStore(), { sessionId: sid, repoFullName: 'owner/demo', baseUrl: 'http://127.0.0.1:3000', exec })

    const flat = calls.map((c) => c.join(' '))
    // No command line anywhere contains the real secret.
    expect(flat.some((c) => c.includes('ghp_REAL_SECRET'))).toBe(false)
    // The clone targets the proxy route, not github.com.
    const clone = calls.find((c) => c[2] === 'git' && c[3] === 'clone')
    expect(clone?.[4]).toContain(`/api/bridge/v1/code/sessions/${sid}/git/owner/demo.git`)
    expect(clone?.[4]).not.toContain('github.com')
    // No -e GH_TOKEN / GITHUB_TOKEN in the worker launch.
    expect(flat.some((c) => /GH_TOKEN=|GITHUB_TOKEN=/.test(c))).toBe(false)
    // Scope persisted for the proxy.
    expect(getSessionGitScope(findSession(getStore(), sid)!)).toEqual(['owner/demo'])
  })
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `npx vitest run tests/bridge/containers-git-proxy.test.ts`
Expected: FAIL — clone still uses `https://x-access-token:ghp_REAL_SECRET@github.com...`, and `GH_TOKEN` present.

- [ ] **Step 3a: Drop `github.com` from the squid allowlist**

In `containers.ts`, edit `DEFAULT_ALLOW` (`:53`): remove the `".github.com"` entry (keep `.githubusercontent.com`). Update the comment to:

```ts
/** Domains a `trusted`/`custom` egress level always allows. GitHub git is
 *  reached ONLY through the host-side scoped-credential proxy (host.docker.
 *  internal, in NO_PROXY), so github.com is intentionally absent here. */
const DEFAULT_ALLOW = [
  '.githubusercontent.com',
  '.npmjs.org',
  'registry.npmjs.org',
  'pypi.org',
  'files.pythonhosted.org',
  'crates.io',
  'static.crates.io',
  '.rubygems.org',
  '.debian.org',
  '.ubuntu.com',
  'host.docker.internal',
]
```

- [ ] **Step 3b: Generate the cap token + persist scope at container setup**

In `launchContainerSession`, find the `setSessionContainer(store, sessionId, { container: name, repo: repoFullName })` call (`:335`). Replace with a cap token + scope. Add just above the `await step('Set up a cloud container', …)` block (after `epoch` is computed, ~`:221`):

```ts
  const { randomBytes: _rb } = await import('node:crypto')
  const gitCapToken = `git_${_rb(24).toString('base64url')}`
```

Then change the `setSessionContainer` call inside step 1 to:

```ts
    setSessionContainer(store, sessionId, {
      container: name,
      repo: repoFullName,
      extraRepos,
      gitCapToken,
    })
```

- [ ] **Step 3c: Replace `configureGitCreds` with `configureGitProxy`**

Replace the whole `configureGitCreds` function (`:353-372`) with a proxy-credential writer. It no longer embeds the real token; it writes the cap token for the proxy host only:

```ts
  // The proxy base the in-container git talks to (same host:port the child uses
  // for callbacks). owner/repo path is appended per-remote.
  const proxyOrigin = childBaseUrl.replace(/\/+$/, '')
  const proxyRemote = (full: string) => `${proxyOrigin}/api/bridge/v1/code/sessions/${sessionId}/git/${full}.git`
  const proxyHostUrl = new URL(proxyOrigin)
  // Credential helper line: cap token as the password for the proxy host. The
  // REAL PAT is never written into the container — the proxy injects it.
  const credLine = `${proxyHostUrl.protocol}//x-access-token:${gitCapToken}@${proxyHostUrl.host}`

  const configureGitProxy = async (): Promise<void> => {
    const login = gh.login || 'jarvis'
    const email = `${login}@users.noreply.github.com`
    const cmd = [
      `git config --global user.name ${shq(login)}`,
      `git config --global user.email ${shq(email)}`,
      `git config --global credential.helper store`,
      `git config --global init.defaultBranch main`,
      `git config --global --add safe.directory ${shq(workdir)}`,
      `(umask 077; printf '%s\\n' ${shq(credLine)} > "$HOME/.git-credentials")`,
    ].join(' && ')
    try {
      await exec(['exec', name, 'sh', '-c', cmd])
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      emit(store, sessionId, `⚠ git proxy credentials not configured — ${msg.slice(0, 200)}`)
    }
  }
```

Then replace every remaining `configureGitCreds()` call site (the clone step `:407`, and the cache-snapshot re-write `:477`) with `configureGitProxy()`.

- [ ] **Step 3d: Clone through the proxy (primary + extras + cache-hit remote reset)**

In the `step(cacheHit ? 'Restored repository' : 'Cloned repository', …)` block:

Cache-hit branch (`:376-387`) — reset the remote to THIS session's proxy URL before fetching (the baked-in remote points at the previous session's proxy path):

```ts
    if (cacheHit) {
      await exec(['exec', '-w', workdir, name, 'git', 'remote', 'set-url', 'origin', proxyRemote(repoFullName)]).catch(() => {})
      await exec([
        'exec', '-w', workdir, name, 'sh', '-c',
        `base=$(git symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null | sed 's#^origin/##'); [ -z "$base" ] && base=main; git fetch origin >/dev/null 2>&1; git checkout "$base" >/dev/null 2>&1; git reset --hard "origin/$base" >/dev/null 2>&1; git clean -fd >/dev/null 2>&1`,
      ]).catch(() => {})
    } else {
```

Fresh-clone branch (`:388-405`) — clone via the proxy URL; the remote stays the proxy URL (no `set-url` to github.com — there is no token to strip):

```ts
      await exec(['exec', name, 'git', 'clone', proxyRemote(repoFullName), workdir])
    }
    await configureGitProxy()
```

Extra repos (`:410-427`) — clone each via the proxy URL; drop the github.com `set-url`:

```ts
    for (const extra of extraRepos) {
      const edir = `/workspace/${repoDirName(extra)}`
      await exec(['exec', name, 'git', 'clone', proxyRemote(extra), edir]).catch(() => {})
    }
```

- [ ] **Step 3e: Drop `GH_TOKEN`/`GITHUB_TOKEN` from the child env**

In the `childEnv` object (`:628`), delete the line:

```ts
      ...(ghToken && { GH_TOKEN: ghToken, GITHUB_TOKEN: ghToken }),
```

`ghToken` is still fetched at `:345` (host-side, used by the PR path in Task 6); just stop injecting it into the container. If `ghToken` becomes unused after Task 6, the compiler/lint will flag it — keep it; Task 6 uses it host-side.

- [ ] **Step 3f: Update the identity prompt (drop in-container `gh pr create`)**

In the `identityPrompt` string (`:642-653`), replace the two PR sentences ("For substantial work also open a pull request: the gh CLI is authenticated…") with:

```ts
      'Git here is wired through a secure proxy: user.name/email are set and a credential helper authorizes pushes to this session\\'s repository, so git commit and git push work without prompting. Never ask for a git name, email, or credentials. ' +
      'When you finish a unit of work, create a branch named jarvis/<short-topic>, commit, and run git push -u origin <branch>; then tell the user the branch is pushed and the pull request can be opened from the session panel. Do not run gh or attempt GitHub API calls — opening the PR is a host action. ' +
```

(Leave the rest of the prompt unchanged.)

- [ ] **Step 4: Run to verify it passes**

Run: `npx vitest run tests/bridge/containers-git-proxy.test.ts`
Expected: PASS. Then `npx vitest run tests/bridge/containers.test.ts` — fix any test that asserted the OLD token-in-clone / `set-url github.com` behavior (update those expectations to the proxy URL; the PR tests are handled in Task 6).

- [ ] **Step 5: Commit**

```bash
git add src/lib/bridge/containers.ts tests/bridge/containers-git-proxy.test.ts tests/bridge/containers.test.ts
git commit -m "feat(code): clone/push via git proxy; remove real PAT + GH_TOKEN from container"
```

---

## Task 6: PR / merge move host-side (REST), so the token never returns

**Files:**
- Modify: `src/lib/connectors/github.ts` (add `openPullRequest`, `mergePullRequest`)
- Modify: `src/lib/bridge/containers.ts` (`createContainerPR` `:832`, `mergeContainerPR` `:890`)
- Test: `tests/bridge/containers.test.ts` (update the existing PR/merge tests)

- [ ] **Step 1: Write failing tests for the REST helpers** (append to `tests/bridge/github.test.ts`; create the file if absent)

```ts
import { describe, expect, test, beforeEach, afterEach, vi } from 'vitest'
import { openPullRequest, mergePullRequest } from '@/lib/connectors/github'

// connectors.json read returns a token in this test by stubbing fs is heavy;
// instead stub global fetch and rely on the real load() returning {} → these
// assert the "not connected" + the happy path via a spy on fetch + a fake token.
vi.mock('node:fs', async (orig) => {
  const real = await orig<typeof import('node:fs')>()
  return { ...real, promises: { ...real.promises, readFile: async () => JSON.stringify({ github: { token: 't', login: 'me', connectedAt: 1 } }) } }
})

beforeEach(() => vi.stubGlobal('fetch', vi.fn()))
afterEach(() => vi.unstubAllGlobals())

describe('openPullRequest', () => {
  test('POSTs /pulls and returns url+number', async () => {
    ;(fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(new Response(JSON.stringify({ html_url: 'https://gh/pr/1', number: 1 }), { status: 201 }))
    const r = await openPullRequest('owner/demo', 'jarvis/x', 'main', 'T', 'B')
    expect(r).toEqual({ ok: true, url: 'https://gh/pr/1', number: 1 })
    const [url, init] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(String(url)).toBe('https://api.github.com/repos/owner/demo/pulls')
    expect(init.method).toBe('POST')
  })
  test('422 (exists) → finds the open PR', async () => {
    ;(fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(new Response('dup', { status: 422 }))
      .mockResolvedValueOnce(new Response(JSON.stringify([{ html_url: 'https://gh/pr/9', number: 9 }]), { status: 200 }))
    const r = await openPullRequest('owner/demo', 'jarvis/x', 'main', 'T', 'B')
    expect(r).toEqual({ ok: true, url: 'https://gh/pr/9', number: 9 })
  })
})

describe('mergePullRequest', () => {
  test('PUTs /merge', async () => {
    ;(fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(new Response('{}', { status: 200 }))
    expect(await mergePullRequest('owner/demo', 9)).toEqual({ ok: true })
    const [url, init] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(String(url)).toBe('https://api.github.com/repos/owner/demo/pulls/9/merge')
    expect(init.method).toBe('PUT')
  })
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `npx vitest run tests/bridge/github.test.ts`
Expected: FAIL — `openPullRequest is not exported`.

- [ ] **Step 3: Add the REST helpers to `github.ts`**

Append to `src/lib/connectors/github.ts`:

```ts
/** Open a PR head→base (host-side, real PAT). On 422 (already exists) returns the open PR. */
export async function openPullRequest(
  repo: string,
  head: string,
  base: string,
  title: string,
  body: string,
  draft = false,
): Promise<{ ok: true; url: string; number: number } | { ok: false; error: string }> {
  const c = await load()
  if (!c.github) return { ok: false, error: 'GitHub not connected' }
  try {
    const r = await fetch(`${GH}/repos/${repo}/pulls`, {
      method: 'POST',
      headers: ghHeaders(c.github.token),
      body: JSON.stringify({ title, head, base, body, draft }),
    })
    if (r.status === 422) {
      const owner = repo.split('/')[0]
      const ex = await fetch(`${GH}/repos/${repo}/pulls?head=${owner}:${head}&state=open&per_page=1`, { headers: ghHeaders(c.github.token) })
      if (ex.ok) {
        const a = (await ex.json()) as Array<{ html_url?: string; number?: number }>
        if (a[0]) return { ok: true, url: String(a[0].html_url ?? ''), number: Number(a[0].number) }
      }
      return { ok: false, error: 'A pull request already exists for this branch.' }
    }
    if (!r.ok) return { ok: false, error: `GitHub error ${r.status}` }
    const j = (await r.json()) as { html_url?: string; number?: number }
    return { ok: true, url: String(j.html_url ?? ''), number: Number(j.number) }
  } catch (e) {
    return { ok: false, error: `Network error: ${String(e)}` }
  }
}

/** Squash-merge a PR by number (host-side, real PAT). */
export async function mergePullRequest(
  repo: string,
  number: number,
  method: 'squash' | 'merge' | 'rebase' = 'squash',
): Promise<{ ok: true } | { ok: false; error: string }> {
  const c = await load()
  if (!c.github) return { ok: false, error: 'GitHub not connected' }
  try {
    const r = await fetch(`${GH}/repos/${repo}/pulls/${number}/merge`, {
      method: 'PUT',
      headers: ghHeaders(c.github.token),
      body: JSON.stringify({ merge_method: method }),
    })
    if (!r.ok) return { ok: false, error: `Merge not allowed (${r.status}) — checks pending or branch protected.` }
    return { ok: true }
  } catch (e) {
    return { ok: false, error: `Network error: ${String(e)}` }
  }
}
```

- [ ] **Step 4: Run to verify the helpers pass**

Run: `npx vitest run tests/bridge/github.test.ts`
Expected: PASS (4 tests).

- [ ] **Step 5: Refactor `createContainerPR` — push in-container, open PR host-side**

In `containers.ts`, change `createContainerPR` so the in-container script ONLY pushes + reports branch/base/repo (drop the in-container `gh pr create` / `gh pr view` lines), then call `openPullRequest` host-side. Replace the body from the `prLines` definition through the end of the function with:

```ts
  const branch = `jarvis/session-${sessionId.slice(0, 8)}`
  const msg = 'Changes from a Jarvis /code session'
  const script = [
    `cd ${workdir} || exit 1`,
    `base=$(git symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null | sed 's#^origin/##'); [ -z "$base" ] && base=main`,
    `cur=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)`,
    `if [ "$cur" = "$base" ] || [ -z "$cur" ] || [ "$cur" = "HEAD" ]; then git checkout -b ${shq(branch)} 2>/dev/null || git checkout ${shq(branch)} 2>/dev/null; cur=$(git rev-parse --abbrev-ref HEAD 2>/dev/null); fi`,
    `if [ -n "$(git status --porcelain)" ]; then git add -A && git commit -m ${shq(msg)} >/dev/null 2>&1; fi`,
    `git push -u origin "$cur" >/dev/null 2>&1`,
    `printf '@@BASE@@%s\\n' "$base"`,
    `printf '@@BRANCH@@%s\\n' "$cur"`,
  ].join('\n')
  let out: string
  try {
    out = (await exec(['exec', meta.container, 'sh', '-c', script])).stdout
  } catch (e) {
    return { error: e instanceof Error ? e.message : String(e) }
  }
  const base = /@@BASE@@(.*)/.exec(out)?.[1]?.trim() || 'main'
  const cur = /@@BRANCH@@(.*)/.exec(out)?.[1]?.trim() || branch

  if (mode === 'compose') {
    return { url: `https://github.com/${meta.repo}/compare/${base}...${cur}?expand=1`, branch: cur }
  }
  const { openPullRequest } = await import('../connectors/github')
  const pr = await openPullRequest(meta.repo, cur, base, 'Changes from a Jarvis /code session', `From a Jarvis /code session.`, mode === 'draft')
  if (!pr.ok) {
    // Fall back to the compare URL the user can click.
    return { url: `https://github.com/${meta.repo}/compare/${base}...${cur}?expand=1`, branch: cur }
  }
  return { url: pr.url, branch: cur }
```

- [ ] **Step 6: Refactor `mergeContainerPR` — read branch in-container, merge host-side**

Replace `mergeContainerPR`'s body (`:890-915`) with:

```ts
  const session = findSession(store, sessionId)
  const meta = session?.container_json
    ? (JSON.parse(session.container_json) as { container?: string; repo?: string })
    : null
  if (!meta?.container || !meta.repo) return { error: 'This session has no container.' }
  const workdir = `/workspace/${repoDirName(meta.repo)}`
  let cur: string
  try {
    cur = (await exec(['exec', meta.container, 'sh', '-c', `cd ${workdir} && git rev-parse --abbrev-ref HEAD 2>/dev/null`])).stdout.trim()
  } catch (e) {
    return { error: e instanceof Error ? e.message : String(e) }
  }
  if (!cur) return { error: 'No branch to merge.' }
  const { githubPrStatus, mergePullRequest } = await import('../connectors/github')
  const status = await githubPrStatus(meta.repo, cur)
  if (!status.ok || !status.status.pr) return { error: 'No open pull request for this branch.' }
  const merged = await mergePullRequest(meta.repo, status.status.pr.number)
  return merged.ok ? { merged: true } : { error: merged.error }
```

- [ ] **Step 7: Update the existing PR/merge tests + run**

In `tests/bridge/containers.test.ts`, the `createContainerPR`/`mergeContainerPR` tests currently assert the in-container `gh` output (`@@PRURL@@`). Update them: mock `@/lib/connectors/github` to also export `openPullRequest`/`mergePullRequest`/`githubPrStatus` (e.g. `openPullRequest: async () => ({ ok: true, url: 'https://gh/pr/1', number: 1 })`), and assert the returned `url` comes from that. The docker mock now only needs to answer the push script with `@@BASE@@main` / `@@BRANCH@@jarvis/...` on stdout.

Run: `npx vitest run tests/bridge/containers.test.ts tests/bridge/github.test.ts`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/lib/connectors/github.ts src/lib/bridge/containers.ts tests/bridge/github.test.ts tests/bridge/containers.test.ts
git commit -m "feat(code): open/merge PRs host-side via REST (no GitHub token in container)"
```

---

## Task 7: Full-suite green + typecheck

**Files:** none (verification only)

- [ ] **Step 1: Typecheck**

Run: `npx tsc --noEmit`
Expected: no errors. (If `ghToken` is reported unused in `containers.ts`, confirm it is still referenced by the clone-fallback/host path; if genuinely unused, remove its now-dead uses — but the PR path keeps `getGithubToken` host-side.)

- [ ] **Step 2: Full web test suite**

Run: `npm test`
Expected: all pass (new `git-proxy`, `containers-git-proxy`, `github`, updated `containers`, `store`).

- [ ] **Step 3: Commit (if any test fixups were needed)**

```bash
git add -A
git commit -m "test(code): green suite for scoped-credential git proxy"
```

---

## Self-review notes (coverage vs spec)

- **Token never in container** → Task 5 (cap-token `.git-credentials`, proxy clone URL, no `GH_TOKEN`) + Task 6 (PR/merge host-side). Verified by the `ghp_REAL_SECRET` absence assertion.
- **Repo + operation scope** → Task 1 (`parseGitRequest` op from URL) + Task 4 (`assertRepoAllowed` 403 + audit).
- **Real PAT injected upstream** → Task 2 (`forwardToGithub` Basic auth).
- **Protocol v2 + content-type** → Task 2 forwards `git-protocol`; preserves upstream `content-type`; no `accept-encoding` (gzip-trap avoidance).
- **Squid drops github.com** → Task 5 Step 3a.
- **Audit** → Task 4 (403 `status` event + `console.info` per data op).
- **Out of scope (unchanged):** branch enforcement, local worker, REST proxy, GitHub App — none touched.
- **Type consistency:** `GitRequest`/`GitService`, `setSessionContainer({container,repo,extraRepos?,gitCapToken?})`, `getSessionGitScope`, `validateGitCapToken`, `forwardToGithub(req,target,pat)`, `openPullRequest(repo,head,base,title,body,draft?)`, `mergePullRequest(repo,number,method?)` are used identically across tasks.
```
