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

  it('does not over-match neighboring or deeper paths', async () => {
    for (const [method, path] of [
      ['GET', '/api/bridge/v1/environments'], //          web-UI env list (cookie auth)
      ['POST', '/api/bridge/v1/environments/env_1/config'],
      ['POST', '/api/bridge/v1/admin/enqueue'], //        unauthenticated by design — must stay gated
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
