import { describe, expect, test, beforeEach, afterEach, vi } from 'vitest'
import { _resetForTests, getStore } from '@/lib/bridge/db'
import {
  createEnvironment,
  findEnvironment,
  findSession,
  listEnvironments,
  listInboundSince,
  listSessionEvents,
  parseEnvironmentConfig,
  setEnvironmentConfig,
} from '@/lib/bridge/store'
import * as containers from '@/lib/bridge/containers'
import { getUserId } from '@/lib/auth-helpers'

// The dispatch route is SERVICE-token only — it must never consult the browser
// session. Mocked so we can assert getUserId is never even called.
vi.mock('@/lib/auth-helpers', () => ({ getUserId: vi.fn(async () => 'browser-user') }))
// LOCAL_USER_ID without dragging the drizzle/Postgres graph into the test.
vi.mock('@/lib/chat/persist', () => ({
  LOCAL_USER_ID: '00000000-0000-0000-0000-000000000001',
}))

const SVC = 'svc_gh_app_bridge_token'
const LOCAL_USER = '00000000-0000-0000-0000-000000000001'

// The service token rides its OWN header (X-GH-App-Token) so Authorization
// stays free for proxy.ts's network bearer gate (JARVIS_REQUIRE_LOCAL_AUTH=1
// requires `Authorization: Bearer <JARVIS_LOCAL_API_TOKEN>` on every /api/*
// request — same header, different value, so the two must not collide).
// Every request here carries an unrelated Authorization bearer to prove the
// route never reads the service token from it.
function post(body: unknown, token?: string): Request {
  return new Request('http://web:3000/api/bridge/v1/gh-app/dispatch', {
    method: 'POST',
    headers: {
      'content-type': 'application/json',
      authorization: 'Bearer local-api-token-for-proxy',
      ...(token !== undefined ? { 'x-gh-app-token': token } : {}),
    },
    body: JSON.stringify(body),
  })
}

const validBody = {
  repo: 'octo/maxrun',
  installationToken: 'ghs_inst_tok',
  botLogin: 'talos',
  task: 'fix the flaky test in ci.yml',
  publicOrigin: 'https://0wlan.com/',
  model: 'claude-opus-4-8',
}

async function route() {
  return import('@/app/api/bridge/v1/gh-app/dispatch/route')
}

beforeEach(() => {
  _resetForTests()
  process.env.GH_APP_BRIDGE_TOKEN = SVC
  vi.mocked(getUserId).mockClear()
})
afterEach(() => {
  delete process.env.GH_APP_BRIDGE_TOKEN
  delete process.env.GH_APP_WEB_URL
  delete process.env.JARVIS_CODE_INTERNAL_ORIGIN
  delete process.env.JARVIS_GH_APP_SESSION_OWNER
  vi.restoreAllMocks()
})

describe('POST /api/bridge/v1/gh-app/dispatch', () => {
  test('401 on a missing or wrong X-GH-App-Token; getUserId is never consulted', async () => {
    const { POST } = await route()
    expect((await POST(post(validBody))).status).toBe(401)
    expect((await POST(post(validBody, 'wrong-token'))).status).toBe(401)
    expect(vi.mocked(getUserId)).not.toHaveBeenCalled()
  })

  test('401 when the service token is sent via Authorization: Bearer (the proxy.ts header) instead of X-GH-App-Token', async () => {
    const { POST } = await route()
    const req = new Request('http://web:3000/api/bridge/v1/gh-app/dispatch', {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        authorization: `Bearer ${SVC}`, // correct value, wrong header
      },
      body: JSON.stringify(validBody),
    })
    expect((await POST(req)).status).toBe(401)
  })

  test('401 (inert) when GH_APP_BRIDGE_TOKEN is not configured — even with a matching bearer', async () => {
    delete process.env.GH_APP_BRIDGE_TOKEN
    const { POST } = await route()
    expect((await POST(post(validBody, ''))).status).toBe(401)
    expect((await POST(post(validBody, SVC))).status).toBe(401)
  })

  test('valid token + payload → env for the repo, seeded session, launch with origin + token', async () => {
    const launchSpy = vi
      .spyOn(containers, 'launchContainerSession')
      .mockResolvedValue(undefined)
    const { POST } = await route()
    const res = await POST(post(validBody, SVC))
    expect(res.status).toBe(200)
    const j = (await res.json()) as { session_id: string; session_url: string }
    expect(j.session_id).toMatch(/^[0-9a-f]{16}$/)
    // Trailing slash on publicOrigin is normalized away.
    expect(j.session_url).toBe(`https://0wlan.com/code/session_${j.session_id}`)

    const store = getStore()
    // Environment: container-typed, owned by the box's single user, repo URL
    // set — and DEDICATED to the bot (machine_name marker), never a user env.
    const envs = listEnvironments(store, LOCAL_USER)
    expect(envs).toHaveLength(1)
    expect(envs[0]).toMatchObject({
      worker_type: 'container',
      machine_name: 'gh-app-bot',
      git_repo_url: 'https://github.com/octo/maxrun',
      user_id: LOCAL_USER,
    })
    // Bot envs are locked down: allowlist egress (not --network=host), and no
    // user-configured env vars / setup script can ever leak into a bot job.
    expect(parseEnvironmentConfig(envs[0])).toEqual({
      envVars: {},
      setupScript: '',
      networkLevel: 'trusted',
      customAllowlist: [],
    })
    // Session row exists, titled from the task, attached to that env.
    const session = findSession(store, j.session_id)
    expect(session?.environment_id).toBe(envs[0].environment_id)
    expect(session?.title).toBe('fix the flaky test in ci.yml')

    // Seed order (the child replays inbound from seq 0): bypassPermissions
    // FIRST, then the task as the user message.
    const inbound = listInboundSince(store, j.session_id, 0).map(
      (r) => JSON.parse(r.payload_json) as Record<string, unknown>,
    )
    expect(inbound).toHaveLength(2)
    expect(inbound[0]).toMatchObject({
      type: 'control_request',
      request: { subtype: 'set_permission_mode', mode: 'bypassPermissions' },
    })
    expect(inbound[1]).toMatchObject({ type: 'user' })
    expect(JSON.stringify(inbound[1])).toContain('fix the flaky test in ci.yml')
    // The prompt is mirrored as a session event so the transcript is watchable.
    const events = listSessionEvents(store, j.session_id, 0).map((e) => e.payload_json)
    expect(events.some((p) => p.includes('user_prompt') && p.includes('fix the flaky test'))).toBe(true)

    // Launch got the EXPLICIT public origin (never req.url) + the injected
    // token/bot identity + the model — so A1/A2 pick them up from the meta.
    expect(launchSpy).toHaveBeenCalledTimes(1)
    expect(launchSpy.mock.calls[0][1]).toMatchObject({
      sessionId: j.session_id,
      repoFullName: 'octo/maxrun',
      baseUrl: 'https://0wlan.com',
      model: 'claude-opus-4-8',
      installationToken: 'ghs_inst_tok',
      botLogin: 'talos',
    })
    // Service-token route: the browser-session path is never touched.
    expect(vi.mocked(getUserId)).not.toHaveBeenCalled()
  })

  test('JARVIS_GH_APP_SESSION_OWNER makes the bot env owned by the box user (visible/watchable in their /code UI)', async () => {
    const OWNER = 'a6825d5c-4b5a-4231-867e-44964ab9685e'
    process.env.JARVIS_GH_APP_SESSION_OWNER = OWNER
    vi.spyOn(containers, 'launchContainerSession').mockResolvedValue(undefined)
    const { POST } = await route()
    expect((await POST(post(validBody, SVC))).status).toBe(200)
    const store = getStore()
    // The env is owned by the configured box user — so it lists for THEM…
    const owned = listEnvironments(store, OWNER)
    expect(owned).toHaveLength(1)
    expect(owned[0]).toMatchObject({ machine_name: 'gh-app-bot', user_id: OWNER })
    // …and NOT for the LOCAL_USER default.
    expect(listEnvironments(store, LOCAL_USER)).toHaveLength(0)
  })

  test('threads the internal origin (GH_APP_WEB_URL) into launch as internalBaseUrl; public origin unchanged', async () => {
    process.env.GH_APP_WEB_URL = 'http://web:3000'
    const launchSpy = vi.spyOn(containers, 'launchContainerSession').mockResolvedValue(undefined)
    const { POST } = await route()
    expect((await POST(post(validBody, SVC))).status).toBe(200)
    expect(launchSpy).toHaveBeenCalledTimes(1)
    expect(launchSpy.mock.calls[0][1]).toMatchObject({
      baseUrl: 'https://0wlan.com', // PUBLIC session URL — unchanged
      internalBaseUrl: 'http://web:3000', // container-facing origin — threaded
    })
  })

  test('JARVIS_CODE_INTERNAL_ORIGIN takes precedence over GH_APP_WEB_URL', async () => {
    process.env.GH_APP_WEB_URL = 'http://web:3000'
    process.env.JARVIS_CODE_INTERNAL_ORIGIN = 'http://web-canary:3000'
    const launchSpy = vi.spyOn(containers, 'launchContainerSession').mockResolvedValue(undefined)
    const { POST } = await route()
    expect((await POST(post(validBody, SVC))).status).toBe(200)
    expect(launchSpy.mock.calls[0][1]).toMatchObject({ internalBaseUrl: 'http://web-canary:3000' })
  })

  test('a dispatch NEVER reuses a user /code env for the same repo (secrets + host network stay out of bot jobs)', async () => {
    const store = getStore()
    // A user-configured env for the SAME repo: secret env vars, a setup
    // script, and the default full (--network=host) egress.
    const userEnv = createEnvironment(store, {
      machine_name: 'Cloud container',
      directory: '/workspace',
      git_repo_url: 'https://github.com/octo/maxrun',
      max_sessions: 4,
      worker_type: 'container',
      user_id: LOCAL_USER,
    })
    setEnvironmentConfig(store, userEnv.environment_id, {
      envVars: { SECRET_API_KEY: 'user-secret' },
      setupScript: 'echo user-setup',
      networkLevel: 'full',
      customAllowlist: [],
    })

    vi.spyOn(containers, 'launchContainerSession').mockResolvedValue(undefined)
    const { POST } = await route()
    const res = await POST(post(validBody, SVC))
    expect(res.status).toBe(200)
    const { session_id } = (await res.json()) as { session_id: string }

    // The bot session is attached to a NEW dedicated env, not the user's.
    const session = findSession(store, session_id)
    expect(session?.environment_id).toBeTruthy()
    expect(session?.environment_id).not.toBe(userEnv.environment_id)
    const botEnv = findEnvironment(store, session!.environment_id!)
    expect(botEnv?.machine_name).toBe('gh-app-bot')
    // Locked-down config: trusted egress, NO inherited user secrets/setup.
    const cfg = parseEnvironmentConfig(botEnv)
    expect(cfg.networkLevel).toBe('trusted')
    expect(cfg.envVars).toEqual({})
    expect(cfg.setupScript).toBe('')
    // The user env is untouched.
    expect(parseEnvironmentConfig(findEnvironment(store, userEnv.environment_id))).toMatchObject({
      envVars: { SECRET_API_KEY: 'user-secret' },
      networkLevel: 'full',
    })
  })

  test('a second dispatch for the same repo reuses the environment', async () => {
    vi.spyOn(containers, 'launchContainerSession').mockResolvedValue(undefined)
    const { POST } = await route()
    const r1 = await POST(post(validBody, SVC))
    const r2 = await POST(post(validBody, SVC))
    expect(r1.status).toBe(200)
    expect(r2.status).toBe(200)
    const envs = listEnvironments(getStore(), LOCAL_USER)
    expect(envs).toHaveLength(1)
    // …but each dispatch is its own session.
    const [a, b] = [await r1.json(), await r2.json()] as Array<{ session_id: string }>
    expect(a.session_id).not.toBe(b.session_id)
  })

  test('400 on a bad repo, missing installationToken, missing botLogin, missing task, or bad publicOrigin', async () => {
    const launchSpy = vi
      .spyOn(containers, 'launchContainerSession')
      .mockResolvedValue(undefined)
    const { POST } = await route()
    const cases = [
      { ...validBody, repo: 'not-a-repo' },
      { ...validBody, repo: 'owner/../etc' },
      { ...validBody, repo: 'owner/..' }, // dot-only segment = traversal
      { ...validBody, installationToken: '' },
      // External commits must be attributed to the App bot, never the box
      // owner's connected identity — a tokenless botLogin is a hard 400.
      { ...validBody, botLogin: '' },
      { ...validBody, botLogin: '   ' },
      { ...validBody, botLogin: undefined },
      { ...validBody, task: '   ' },
      { ...validBody, publicOrigin: 'web:3000' },
      { ...validBody, publicOrigin: '' },
      // Degenerate/unparseable origins: new URL() must accept AND yield a
      // real http(s) host — 'https://' alone parses nowhere.
      { ...validBody, publicOrigin: 'https://' },
      { ...validBody, publicOrigin: 'http://' },
      { ...validBody, publicOrigin: 'https://:443' },
      { ...validBody, publicOrigin: 'https:// evil.com' },
    ]
    for (const body of cases) {
      expect((await POST(post(body, SVC))).status).toBe(400)
    }
    expect(launchSpy).not.toHaveBeenCalled()
  })
})
