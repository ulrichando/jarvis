import { describe, expect, test, beforeEach } from 'vitest'
import { _resetForTests, getStore } from '@/lib/bridge/db'
import { createEnvironment, getOrCreateBridgeToken } from '@/lib/bridge/store'
import { POST } from '@/app/api/bridge/v1/admin/enqueue/route'

// Finding #3: admin/enqueue used to be anonymous behind a stale "loopback-only"
// comment. On the Cloudflare-fronted box that assumption is false, so any
// shared-token holder could inject work into ANY environment cross-user. The
// route now fails CLOSED: owner bridge token, the env's own secret, or the
// shared infra token — mirroring the sessions POST gate.

const OWNER = '00000000-0000-0000-0000-0000000000a1'
const OTHER = '00000000-0000-0000-0000-0000000000b2'
const URL_ = 'http://127.0.0.1:3000/api/bridge/v1/admin/enqueue'

function req(init: { token?: string; body?: unknown }): Request {
  return new Request(URL_, {
    method: 'POST',
    headers: {
      'content-type': 'application/json',
      ...(init.token ? { authorization: `Bearer ${init.token}` } : {}),
    },
    body: JSON.stringify(init.body ?? {}),
  })
}

function makeEnv(userId: string) {
  return createEnvironment(getStore(), {
    machine_name: `m-${userId}`,
    directory: '/workspace',
    git_repo_url: 'https://github.com/owner/demo',
    max_sessions: 4,
    worker_type: 'container',
    user_id: userId,
  })
}

const bodyFor = (environment_id: string) => ({ environment_id, session_id: 's1' })

beforeEach(() => _resetForTests())

describe('admin/enqueue auth (finding #3)', () => {
  test('401 without a bearer', async () => {
    const env = makeEnv(OWNER)
    const r = await POST(req({ body: bodyFor(env.environment_id) }))
    expect(r.status).toBe(401)
  })

  test('401 for an unresolvable junk bearer', async () => {
    const env = makeEnv(OWNER)
    const r = await POST(req({ token: 'jbr_bogus', body: bodyFor(env.environment_id) }))
    expect(r.status).toBe(401)
  })

  test("403 for another user's valid bridge token (not the env owner)", async () => {
    const env = makeEnv(OWNER)
    const otherToken = getOrCreateBridgeToken(getStore(), OTHER)
    const r = await POST(req({ token: otherToken, body: bodyFor(env.environment_id) }))
    expect(r.status).toBe(403)
  })

  test('200 for the env owner bridge token', async () => {
    const env = makeEnv(OWNER)
    const ownerToken = getOrCreateBridgeToken(getStore(), OWNER)
    const r = await POST(req({ token: ownerToken, body: bodyFor(env.environment_id) }))
    expect(r.status).toBe(200)
    const body = (await r.json()) as { work_id?: unknown }
    expect(typeof body.work_id).toBe('string')
  })

  test('200 for the environment secret', async () => {
    const env = makeEnv(OWNER)
    const r = await POST(
      req({ token: env.environment_secret, body: bodyFor(env.environment_id) }),
    )
    expect(r.status).toBe(200)
  })

  test('404 for an unknown environment (valid owner token, bad env id)', async () => {
    makeEnv(OWNER)
    const ownerToken = getOrCreateBridgeToken(getStore(), OWNER)
    const r = await POST(req({ token: ownerToken, body: bodyFor('env_does_not_exist') }))
    expect(r.status).toBe(404)
  })

  test('400 for a malformed body (valid owner token)', async () => {
    makeEnv(OWNER)
    const ownerToken = getOrCreateBridgeToken(getStore(), OWNER)
    const r = await POST(req({ token: ownerToken, body: { nope: true } }))
    expect(r.status).toBe(400)
  })
})
