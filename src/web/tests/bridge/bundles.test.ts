import { describe, expect, test, beforeEach, afterAll, vi } from 'vitest'
import { mkdtempSync, rmSync, symlinkSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { randomUUID } from 'node:crypto'
import type { DockerExec } from '@/lib/bridge/containers'

// Seed-bundle store + bundle-mode session plumbing (ultraplan local-bundle MVP).
// Pins: (a) bundlePath() traversal/missing-file hardening, (b) the sessions
// route distinguishing "no bundle requested" from "bundle requested but
// unresolvable" (the latter must 4xx, never silently launch a scratch
// container), (c) the container seed command's squashed-tier fallback +
// empty-workspace verification.

// BUNDLES_ROOT is a module-load const — point it at a temp dir BEFORE the
// module (or anything that imports it) loads. Static vitest/node imports above
// don't read it; everything under test is imported dynamically below.
const ROOT = mkdtempSync(path.join(os.tmpdir(), 'jarvis-bundles-test-'))
process.env.JARVIS_BUNDLES_ROOT = ROOT

// Same hermetic mocks as containers.test.ts — launchContainerSession consults
// github status + MCP connectors during setup.
vi.mock('@/lib/auth-helpers', () => ({
  getUserId: async () => '00000000-0000-0000-0000-000000000001',
  getUserIdOrSharedLocal: async () => '00000000-0000-0000-0000-000000000001',
}))
vi.mock('@/lib/connectors/github', () => ({
  getGithubToken: async () => 'ghp_test_token',
  githubStatus: async () => ({ connected: true, login: 'tester' }),
  openPullRequest: vi.fn(async () => ({ ok: true, url: 'https://github.com/owner/demo/pull/7', number: 7 })),
  mergePullRequest: vi.fn(async () => ({ ok: true })),
  githubPrStatus: vi.fn(async () => ({ ok: true, status: { pr: null, checks: null, sha: null } })),
}))
vi.mock('@/lib/mcp/store', () => ({ listMcpServers: vi.fn(async () => []) }))

const { bundlePath, writeBundle } = await import('@/lib/bridge/bundles')
const { bundleFromContext, POST } = await import('@/app/api/v1/sessions/route')
const { launchContainerSession } = await import('@/lib/bridge/containers')
const { _resetForTests, getStore } = await import('@/lib/bridge/db')
const { getOrCreateBridgeToken, createEnvironment, getOrCreateSession, listSessionEvents } = await import('@/lib/bridge/store')

const USER = '00000000-0000-0000-0000-000000000001'

beforeEach(() => {
  _resetForTests()
  // No model proxy in tests — deterministic direct path.
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => {
      throw new Error('no proxy in tests')
    }),
  )
})

afterAll(() => {
  rmSync(ROOT, { recursive: true, force: true })
})

// ── (a) bundlePath ─────────────────────────────────────────────────────────

describe('bundlePath', () => {
  test('a written bundle resolves to its as-configured store path', () => {
    const id = writeBundle(Buffer.from('bundle-bytes'))
    expect(bundlePath(id)).toBe(path.join(ROOT, `${id}.bundle`))
  })

  test('non-uuid ids are rejected', () => {
    expect(bundlePath('not-a-uuid')).toBeNull()
    expect(bundlePath('..')).toBeNull()
    expect(bundlePath('')).toBeNull()
    // 36 chars of valid charset but not a real file — realpath check kills it.
    expect(bundlePath('-'.repeat(36))).toBeNull()
  })

  test('traversal shapes are rejected', () => {
    expect(bundlePath('../../etc/passwd')).toBeNull()
    expect(bundlePath('../' + 'a'.repeat(33))).toBeNull()
  })

  test('a valid-format id with no stored file is null', () => {
    expect(bundlePath(randomUUID())).toBeNull()
  })

  test('a symlink planted in the store cannot escape the root', () => {
    const outside = path.join(os.tmpdir(), `jarvis-bundle-escape-${Date.now()}`)
    writeFileSync(outside, 'outside the store')
    const id = randomUUID()
    symlinkSync(outside, path.join(ROOT, `${id}.bundle`))
    try {
      expect(bundlePath(id)).toBeNull()
    } finally {
      rmSync(outside, { force: true })
    }
  })
})

// ── (b) bundleFromContext + the sessions-route error path ──────────────────

describe('bundleFromContext', () => {
  test('absent seed_bundle_file_id → not requested', () => {
    expect(bundleFromContext(undefined)).toEqual({ requested: false, path: null })
    expect(bundleFromContext({})).toEqual({ requested: false, path: null })
    expect(bundleFromContext({ seed_bundle_file_id: 42 })).toEqual({ requested: false, path: null })
  })

  test('present + valid id → requested with a resolved path', () => {
    const id = writeBundle(Buffer.from('bundle-bytes'))
    expect(bundleFromContext({ seed_bundle_file_id: id })).toEqual({
      requested: true,
      path: path.join(ROOT, `${id}.bundle`),
    })
  })

  test('present but unresolvable id → requested with path null (the error case)', () => {
    expect(bundleFromContext({ seed_bundle_file_id: randomUUID() })).toEqual({
      requested: true,
      path: null,
    })
    expect(bundleFromContext({ seed_bundle_file_id: '../../etc/passwd' })).toEqual({
      requested: true,
      path: null,
    })
  })
})

function withEnv(workerType: 'container' | 'bridge' = 'container') {
  const store = getStore()
  const token = getOrCreateBridgeToken(store, USER)
  createEnvironment(store, {
    machine_name: 'Cloud container',
    directory: '/workspace',
    git_repo_url: '',
    max_sessions: 4,
    worker_type: workerType === 'container' ? 'container' : 'jarvis',
    user_id: USER,
  })
  return token
}

describe('POST /api/v1/sessions bundle gating', () => {
  test('a requested-but-unresolvable bundle is 404 bundle_not_found, not a scratch launch', async () => {
    const token = withEnv('container')
    const res = await POST(
      new Request('https://web.test/api/v1/sessions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', authorization: `Bearer ${token}` },
        body: JSON.stringify({
          title: 'bundle session',
          session_context: { sources: [], seed_bundle_file_id: randomUUID() },
          events: [],
        }),
      }),
    )
    expect(res.status).toBe(404)
    const body = (await res.json()) as { error: { type: string } }
    expect(body.error.type).toBe('bundle_not_found')
  })

  test('no bundle requested → normal create proceeds (bridge-worker env)', async () => {
    const token = withEnv('bridge')
    const res = await POST(
      new Request('https://web.test/api/v1/sessions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', authorization: `Bearer ${token}` },
        body: JSON.stringify({ title: 'plain session', session_context: { sources: [] } }),
      }),
    )
    expect(res.status).toBe(201)
  })

  test('a valid uploaded bundle passes the gate (bridge-worker env — no docker)', async () => {
    const token = withEnv('bridge')
    const id = writeBundle(Buffer.from('bundle-bytes'))
    const res = await POST(
      new Request('https://web.test/api/v1/sessions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', authorization: `Bearer ${token}` },
        body: JSON.stringify({
          title: 'bundle session',
          session_context: { sources: [], seed_bundle_file_id: id },
        }),
      }),
    )
    expect(res.status).toBe(201)
  })
})

// ── (c) container seed-command shaping ─────────────────────────────────────

/** Fake docker mirroring containers.test.ts — records calls, injects failures. */
function fakeDocker(opts?: { failOn?: (args: string[]) => boolean }) {
  const calls: string[][] = []
  const exec: DockerExec = async (args) => {
    calls.push(args)
    if (opts?.failOn?.(args)) throw new Error('bundle seed produced an empty workspace')
    if (args.some((a) => a.includes('test -f'))) return { stdout: 'no\n', stderr: '' }
    return { stdout: '', stderr: '' }
  }
  return { calls, exec }
}

function makeContainerSession(): string {
  const store = getStore()
  const env = createEnvironment(store, {
    machine_name: 'Cloud container',
    directory: '/workspace',
    git_repo_url: '',
    max_sessions: 4,
    worker_type: 'container',
    user_id: USER,
  })
  getOrCreateSession(store, 'b0nd1e0011223344', env.environment_id)
  return 'b0nd1e0011223344'
}

describe('launchContainerSession bundle seed', () => {
  test('seed runs clone + squashed-tier refs/seed/root fallback + HEAD verification in one sh -c', async () => {
    const sessionId = makeContainerSession()
    const store = getStore()
    const { calls, exec } = fakeDocker()
    const bp = path.join(ROOT, 'seed-under-test.bundle')

    await launchContainerSession(store, {
      sessionId,
      repoFullName: '', // bundle mode: no git-proxy repo
      baseUrl: 'http://127.0.0.1:3000',
      exec,
      bundlePath: bp,
    })

    const flat = calls.map((c) => c.join(' '))
    // The bundle file is bind-mounted read-only into the workbench.
    const run = flat.find((c) => c.startsWith('run -d'))
    expect(run).toContain(`-v ${bp}:/jarvis-seed.bundle:ro`)

    // One sh -c does clone → fallback → verify, so a silent empty clone
    // (squashed tier: only refs/seed/root, no HEAD) can't produce a lying ✓.
    const seed = flat.find((c) => c.includes('git clone /jarvis-seed.bundle'))
    expect(seed).toBeDefined()
    expect(seed).toContain('sh -c')
    expect(seed).toContain("refs/seed/root")
    expect(seed).toContain('checkout -q FETCH_HEAD')
    expect(seed).toContain('rev-parse --verify -q HEAD')
    expect(seed).toContain('bundle seed produced an empty workspace')
    expect(seed).toContain('exit 1')

    const statuses = listSessionEvents(store, sessionId, 0)
      .map((e) => (JSON.parse(e.payload_json) as { status?: string }).status)
      .filter(Boolean)
    expect(statuses).toContain('✓ Seeded from bundle')
  })

  test('an empty-workspace seed fails the step (✗) and tears down instead of a lying ✓', async () => {
    const sessionId = makeContainerSession()
    const store = getStore()
    const { exec } = fakeDocker({
      failOn: (args) => args.join(' ').includes('git clone /jarvis-seed.bundle'),
    })

    await expect(
      launchContainerSession(store, {
        sessionId,
        repoFullName: '',
        baseUrl: 'http://127.0.0.1:3000',
        exec,
        bundlePath: path.join(ROOT, 'seed-under-test.bundle'),
      }),
    ).rejects.toThrow()

    const statuses = listSessionEvents(store, sessionId, 0)
      .map((e) => (JSON.parse(e.payload_json) as { status?: string }).status)
      .filter(Boolean) as string[]
    expect(statuses.some((s) => s.startsWith('✗ Seeded from bundle'))).toBe(true)
    expect(statuses.some((s) => s.includes('empty workspace'))).toBe(true)
    expect(statuses).not.toContain('✓ Seeded from bundle')
  })
})
