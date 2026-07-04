// src/gh-app/server.test.ts — hermetic smoke of the HTTP surface (fake deps).
import { test, expect, describe } from 'bun:test'
import { createHmac } from 'node:crypto'
import { makeApp, credsFromEnvOrFile, sandboxEnvPassthrough, dockerSpawnContainer, type ServerDeps } from './server.js'
import type { NewJob } from './jobs.js'

const SECRET = 'topsecret'
const sigFor = (b: string) => 'sha256=' + createHmac('sha256', SECRET).update(b).digest('hex')

function harness(over: Partial<ServerDeps> = {}) {
  const enqueued: NewJob[] = []
  const saved: unknown[] = []
  const fetches: string[] = []
  const deps: ServerDeps = {
    base: 'https://gh.0wlan.com',
    webhook: { secret: SECRET, allowlist: ['ulrichando'], enqueue: async (j) => { enqueued.push(j) } },
    fetch: (async (url: string | URL | Request) => {
      fetches.push(String(url))
      return new Response(JSON.stringify({ id: 7, pem: 'PEM', webhook_secret: 'ws' }), { status: 201 })
    }) as typeof fetch,
    saveCreds: async (c) => { saved.push(c) },
    ...over,
  }
  return { app: makeApp(deps), enqueued, saved, fetches }
}

const webhookBody = JSON.stringify({
  action: 'created',
  issue: { number: 3 },
  comment: { body: '@jarvis fix the README', user: { login: 'ulrichando' }, author_association: 'OWNER' },
  repository: { full_name: 'o/r' },
  installation: { id: 42 },
})

describe('gh-app server', () => {
  test('POST /webhook with a signed body → 202 and enqueues', async () => {
    const { app, enqueued } = harness()
    const res = await app(new Request('http://x/webhook', {
      method: 'POST',
      headers: { 'X-Hub-Signature-256': sigFor(webhookBody), 'X-GitHub-Event': 'issue_comment' },
      body: webhookBody,
    }))
    expect(res.status).toBe(202)
    expect(enqueued).toEqual([{ installationId: 42, repo: 'o/r', issueNumber: 3, task: 'fix the README', isPR: false }])
  })

  test('POST /webhook with a bad signature → 401, nothing enqueued', async () => {
    const { app, enqueued } = harness()
    const res = await app(new Request('http://x/webhook', {
      method: 'POST',
      headers: { 'X-Hub-Signature-256': 'sha256=' + '0'.repeat(64), 'X-GitHub-Event': 'issue_comment' },
      body: webhookBody,
    }))
    expect(res.status).toBe(401)
    expect(enqueued.length).toBe(0)
  })

  // GitHub caps webhook payloads at 25 MB — a bigger declared length is not
  // GitHub. Reject BEFORE buffering (the handler previously read the whole
  // body into memory ahead of the signature check).
  test('POST /webhook with oversized Content-Length → 413, nothing enqueued', async () => {
    const { app, enqueued } = harness()
    const res = await app(new Request('http://x/webhook', {
      method: 'POST',
      headers: {
        'content-length': String(30 * 1024 * 1024),
        'X-Hub-Signature-256': sigFor(webhookBody),
        'X-GitHub-Event': 'issue_comment',
      },
      body: webhookBody,
    }))
    expect(res.status).toBe(413)
    expect(enqueued.length).toBe(0)
  })

  test('GET /health → 200', async () => {
    const { app } = harness()
    const res = await app(new Request('http://x/health'))
    expect(res.status).toBe(200)
  })

  test('GET /setup → manifest form pointing at github.com/settings/apps/new', async () => {
    const { app } = harness()
    const res = await app(new Request('http://x/setup'))
    expect(res.status).toBe(200)
    const html = await res.text()
    expect(html).toContain('github.com/settings/apps/new')
    expect(html).toContain('gh.0wlan.com/webhook')
  })

  test('GET /setup/callback?code=… converts + saves creds without echoing secrets', async () => {
    const { app, saved } = harness()
    const res = await app(new Request('http://x/setup/callback?code=abc'))
    expect(res.status).toBe(200)
    expect(saved.length).toBe(1)
    expect((saved[0] as { appId: number }).appId).toBe(7)
    const html = await res.text()
    expect(html).not.toContain('PEM')
    expect(html).not.toContain('ws')
  })

  test('GET /setup/callback without code → 400; unknown path → 404', async () => {
    const { app } = harness()
    expect((await app(new Request('http://x/setup/callback'))).status).toBe(400)
    expect((await app(new Request('http://x/nope'))).status).toBe(404)
  })

  // One-time setup: GitHub can reach the host unauthenticated, so a second
  // callback (attacker-supplied manifest code) must NOT be able to overwrite
  // the captured app credentials — refuse before converting anything.
  test('GET /setup/callback with creds already stored → 409, no conversion, no overwrite', async () => {
    const { app, saved, fetches } = harness({ credsExist: () => true })
    const res = await app(new Request('http://x/setup/callback?code=attacker-code'))
    expect(res.status).toBe(409)
    expect(await res.text()).toContain('already configured')
    expect(saved.length).toBe(0)   // nothing written
    expect(fetches.length).toBe(0) // conversion never even attempted
  })

  test('GET /setup/callback with credsExist=false still converts (first-time setup)', async () => {
    const { app, saved } = harness({ credsExist: async () => false })
    const res = await app(new Request('http://x/setup/callback?code=abc'))
    expect(res.status).toBe(200)
    expect(saved.length).toBe(1)
  })
})

describe('gh-app credsFromEnvOrFile', () => {
  test('env vars win when all three are present', () => {
    const c = credsFromEnvOrFile(
      { GH_APP_ID: '11', GH_APP_PRIVATE_KEY: 'PEM', GH_APP_WEBHOOK_SECRET: 's' },
      () => { throw new Error('no file') },
    )
    expect(c).toEqual({ appId: 11, pem: 'PEM', webhookSecret: 's' })
  })
  test('falls back to the creds file; null when neither exists', () => {
    const file = JSON.stringify({ appId: 12, pem: 'P2', webhookSecret: 's2' })
    expect(credsFromEnvOrFile({}, () => file)).toEqual({ appId: 12, pem: 'P2', webhookSecret: 's2' })
    expect(credsFromEnvOrFile({}, () => { throw new Error('ENOENT') })).toBeNull()
  })
})

describe('gh-app dockerSpawnContainer sandbox network', () => {
  const spec = { image: 'jarvis-gh-app', cmd: ['bun', 'x.ts'], env: {}, timeoutSec: 5, writableWorkdir: true }

  // Raw provider keys ride the job env; the default docker bridge has full
  // internet egress → prompt-injected repo content could exfiltrate them.
  // No network configured = refuse to spawn, never fall open.
  test('refuses to spawn when the sandbox network is unset/empty — no container created', async () => {
    const realFetch = globalThis.fetch
    let dockerCalls = 0
    globalThis.fetch = (async () => { dockerCalls++; return new Response('{}', { status: 201 }) }) as typeof fetch
    try {
      expect(() => dockerSpawnContainer('tcp://docker-proxy:2375', undefined)).toThrow(/GH_APP_SANDBOX_NETWORK/)
      expect(() => dockerSpawnContainer('tcp://docker-proxy:2375', '')).toThrow(/GH_APP_SANDBOX_NETWORK/)
      expect(() => dockerSpawnContainer('tcp://docker-proxy:2375', '   ')).toThrow(/GH_APP_SANDBOX_NETWORK/)
      expect(dockerCalls).toBe(0) // refusal happens before any Docker API call
    } finally {
      globalThis.fetch = realFetch
    }
  })

  test('always sets HostConfig.NetworkMode to the configured network', async () => {
    const realFetch = globalThis.fetch
    const created: any[] = []
    globalThis.fetch = (async (url: string | URL | Request, init?: RequestInit) => {
      const u = String(url)
      if (u.endsWith('/containers/create')) {
        created.push(JSON.parse(String(init?.body)))
        return new Response(JSON.stringify({ Id: 'cid1' }), { status: 201 })
      }
      if (u.includes('/start')) return new Response(null, { status: 204 })
      if (u.includes('/wait')) return new Response(JSON.stringify({ StatusCode: 0 }), { status: 200 })
      if (u.includes('/logs')) return new Response(new Uint8Array(0), { status: 200 })
      return new Response(null, { status: 204 }) // force-remove
    }) as typeof fetch
    try {
      const spawn = dockerSpawnContainer('tcp://docker-proxy:2375', 'jarvis-sandbox-net')
      const r = await spawn(spec)
      expect(r.code).toBe(0)
      expect(created.length).toBe(1)
      expect(created[0].HostConfig.NetworkMode).toBe('jarvis-sandbox-net')
    } finally {
      globalThis.fetch = realFetch
    }
  })
})

describe('gh-app sandboxEnvPassthrough', () => {
  test('forwards only present provider-key vars — never app creds or DB', () => {
    const env = {
      ANTHROPIC_API_KEY: 'a', DEEPSEEK_API_KEY: 'd', JARVIS_PROVIDER: 'deepseek',
      GH_APP_PRIVATE_KEY: 'PEM-NO', GH_APP_WEBHOOK_SECRET: 'no', DATABASE_URL: 'no', POSTGRES_PASSWORD: 'no',
      GROQ_API_KEY: undefined,
    }
    const out = sandboxEnvPassthrough(env)
    expect(out).toEqual({ ANTHROPIC_API_KEY: 'a', DEEPSEEK_API_KEY: 'd', JARVIS_PROVIDER: 'deepseek' })
    expect(Object.keys(out)).not.toContain('GH_APP_PRIVATE_KEY')
    expect(Object.keys(out)).not.toContain('DATABASE_URL')
  })
})

describe('POST /internal/mint-token', () => {
  const withMint = (forRepo?: (r: string) => Promise<{ token: string; expiresAt: string }>) => {
    const minted: string[] = []
    const h = harness({
      mint: {
        serviceToken: 'svc-secret',
        forRepo: async (r: string) => {
          minted.push(r)
          return forRepo ? forRepo(r) : { token: 'ghs_fresh', expiresAt: '2026-07-04T20:00:00Z' }
        },
      },
    })
    return { ...h, minted }
  }
  const mintReq = (token: string | null, body: unknown) =>
    new Request('http://x/internal/mint-token', {
      method: 'POST',
      headers: { 'content-type': 'application/json', ...(token ? { 'X-GH-App-Token': token } : {}) },
      body: JSON.stringify(body),
    })

  test('404 when the mint dep is absent (no creds / no bridge token)', async () => {
    const { app } = harness()
    const res = await app(mintReq('svc-secret', { repo: 'o/r' }))
    expect(res.status).toBe(404)
  })

  test('401 on missing or wrong service token — nothing minted', async () => {
    const { app, minted } = withMint()
    expect((await app(mintReq(null, { repo: 'o/r' }))).status).toBe(401)
    expect((await app(mintReq('wrong', { repo: 'o/r' }))).status).toBe(401)
    expect(minted.length).toBe(0)
  })

  test('400 on a non-owner/name repo — nothing minted', async () => {
    const { app, minted } = withMint()
    expect((await app(mintReq('svc-secret', { repo: 'no-slash' }))).status).toBe(400)
    expect((await app(mintReq('svc-secret', { repo: 'a/b/c' }))).status).toBe(400)
    expect((await app(mintReq('svc-secret', {}))).status).toBe(400)
    expect(minted.length).toBe(0)
  })

  test('mints a fresh token for a valid repo', async () => {
    const { app, minted } = withMint()
    const res = await app(mintReq('svc-secret', { repo: 'ulrichando/maxrun' }))
    expect(res.status).toBe(200)
    expect(await res.json()).toEqual({ token: 'ghs_fresh', expiresAt: '2026-07-04T20:00:00Z' })
    expect(minted).toEqual(['ulrichando/maxrun'])
  })

  test('502 when minting fails — status only, no token material in the body', async () => {
    const { app } = withMint(async () => { throw new Error('installation lookup failed: HTTP 404') })
    const res = await app(mintReq('svc-secret', { repo: 'o/gone' }))
    expect(res.status).toBe(502)
    expect(await res.text()).toBe('mint failed')
  })
})

describe('GET / (service root)', () => {
  test('redirects a human to the Jarvis settings bot card by default', async () => {
    const { app } = harness()
    const res = await app(new Request('http://x/'))
    expect(res.status).toBe(302)
    expect(res.headers.get('location')).toBe('https://0wlan.com/settings?tab=applications')
  })

  test('GH_APP_HOME_URL override wins; non-root junk still 404s', async () => {
    const { app } = harness({ homeUrl: 'https://example.com/bots' })
    const res = await app(new Request('http://x/'))
    expect(res.headers.get('location')).toBe('https://example.com/bots')
    expect((await app(new Request('http://x/nope'))).status).toBe(404)
  })
})
