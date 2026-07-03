// src/gh-app/server.test.ts — hermetic smoke of the HTTP surface (fake deps).
import { test, expect, describe } from 'bun:test'
import { createHmac } from 'node:crypto'
import { makeApp, credsFromEnvOrFile, sandboxEnvPassthrough, type ServerDeps } from './server.js'
import type { NewJob } from './jobs.js'

const SECRET = 'topsecret'
const sigFor = (b: string) => 'sha256=' + createHmac('sha256', SECRET).update(b).digest('hex')

function harness() {
  const enqueued: NewJob[] = []
  const saved: unknown[] = []
  const deps: ServerDeps = {
    base: 'https://gh.0wlan.com',
    webhook: { secret: SECRET, allowlist: ['ulrichando'], enqueue: async (j) => { enqueued.push(j) } },
    fetch: (async () => new Response(JSON.stringify({ id: 7, pem: 'PEM', webhook_secret: 'ws' }), { status: 201 })) as typeof fetch,
    saveCreds: async (c) => { saved.push(c) },
  }
  return { app: makeApp(deps), enqueued, saved }
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
