// @vitest-environment node
import { beforeAll, describe, expect, it } from 'vitest'
import type { NextRequest } from 'next/server'
import { NextRequest as NextRequestCtor } from 'next/server'

// Online hardened config, served at a real domain — the exact shape that
// broke remote control on 2026-07-04 (worker routes 401'd at the gate).
process.env.JARVIS_REQUIRE_LOCAL_AUTH = '1'
process.env.JARVIS_LOCAL_API_TOKEN = 'shared-infra-secret'
process.env.JARVIS_WEB_ALLOWED_HOSTS = '0wlan.com'
delete process.env.JARVIS_AUTH_DISABLED

let proxy: (req: NextRequest) => Response
beforeAll(async () => {
  ;({ proxy } = (await import('@/proxy')) as unknown as { proxy: (req: NextRequest) => Response })
})

function req(method: string, path: string, bearer = 'jbr_user'): NextRequest {
  return new NextRequestCtor(`https://0wlan.com${path}`, {
    method,
    headers: { host: '0wlan.com', authorization: `Bearer ${bearer}` },
  })
}
async function authRequired(res: Response): Promise<boolean> {
  if (res.status !== 401) return false
  const b = (await res.clone().json().catch(() => null)) as { error?: string } | null
  return b?.error === 'auth required'
}

describe('proxy: remote-control worker routes reach their own auth (jarvis remote-control)', () => {
  it('waives the shared-token gate for the worker lifecycle routes', async () => {
    for (const [method, path] of [
      ['POST', '/api/bridge/v1/environments/bridge'], //            register
      ['DELETE', '/api/bridge/v1/environments/bridge/env_1'], //    deregister
      ['POST', '/api/bridge/v1/environments/env_1/bridge/reconnect'],
      ['GET', '/api/bridge/v1/environments/env_1/work/poll'],
      ['POST', '/api/bridge/v1/environments/env_1/work/w1/ack'],
      ['POST', '/api/bridge/v1/environments/env_1/work/w1/heartbeat'],
      ['POST', '/api/bridge/v1/environments/env_1/work/w1/stop'],
    ] as const) {
      expect(
        await authRequired(proxy(req(method, path))),
        `${method} ${path} must reach its in-handler auth`,
      ).toBe(false)
    }
  })

  it('waives POST session events/archive but keeps their GET gated', async () => {
    expect(await authRequired(proxy(req('POST', '/api/bridge/v1/sessions/s1/events')))).toBe(false)
    expect(await authRequired(proxy(req('POST', '/api/bridge/v1/sessions/s1/archive')))).toBe(false)
    // events GET (transcript read) has no in-handler bearer auth — must stay gated.
    expect(await authRequired(proxy(req('GET', '/api/bridge/v1/sessions/s1/events')))).toBe(true)
  })

  it('waives the REPL-bridge CCR session lifecycle (create POST, retitle PATCH, archive POST)', async () => {
    expect(await authRequired(proxy(req('POST', '/api/v1/sessions')))).toBe(false)
    expect(await authRequired(proxy(req('PATCH', '/api/v1/sessions/s1')))).toBe(false)
    expect(await authRequired(proxy(req('POST', '/api/v1/sessions/s1/archive')))).toBe(false)
    // Other mutations on the same paths stay gated.
    expect(await authRequired(proxy(req('DELETE', '/api/v1/sessions/s1')))).toBe(true)
    expect(await authRequired(proxy(req('PATCH', '/api/v1/sessions/s1/archive')))).toBe(true)
    expect(await authRequired(proxy(req('POST', '/api/v1/sessions/s1/events')))).toBe(true)
  })

  it('waives POST + GET on the bridge-base sessions (create + web_code list, self-authed)', async () => {
    // initReplBridge posts session create to the BRIDGE base; the voice agent's
    // web_code tool GETs the list. Both self-authenticate in-handler now.
    expect(await authRequired(proxy(req('POST', '/api/bridge/v1/sessions')))).toBe(false)
    expect(await authRequired(proxy(req('GET', '/api/bridge/v1/sessions')))).toBe(false)
  })

  it('waives the voice→web control surface (self-authed in-handler)', async () => {
    for (const [method, path] of [
      ['GET', '/api/workspace'],
      ['POST', '/api/workspace'],
      ['DELETE', '/api/workspace/ws_1'],
      ['GET', '/api/projects'],
      ['PATCH', '/api/projects/p_1'],
      ['GET', '/api/bridge/v1/routines'],
      ['POST', '/api/bridge/v1/routines/r_1/run'],
      ['POST', '/api/bridge/v1/tasks'],
      ['GET', '/api/bridge/v1/environments'],
    ] as const) {
      expect(
        await authRequired(proxy(req(method, path))),
        `${method} ${path} should be waived (self-auth)`,
      ).toBe(false)
    }
  })

  it('does not over-match — high-risk + deeper paths STAY gated', async () => {
    for (const [method, path] of [
      // The voice patterns are `$`-anchored: sub-routes keep more segments.
      ['POST', '/api/workspace/ws_1/exec'], //            arbitrary container shell
      ['GET', '/api/workspace/ws_1/file'], //             file read (key exfil)
      ['POST', '/api/workspace/ws_1/deploy'],
      ['GET', '/api/projects/p_1/conversations'], //      cookie-scoped sub-resource
      ['GET', '/api/bridge/v1/routines/r_1/runs'], //     run history (cookie-authed)
      ['POST', '/api/bridge/v1/environments/env_1/config'],
      ['POST', '/api/bridge/v1/admin/enqueue'], //        unauthenticated by design
      ['POST', '/api/bridge/v1/environments/env_1/work/w1/ack/extra'],
      ['DELETE', '/api/bridge/v1/environments/bridge/env_1/extra'],
    ] as const) {
      expect(
        await authRequired(proxy(req(method, path))),
        `${method} ${path} must stay behind the gate`,
      ).toBe(true)
    }
  })
})
