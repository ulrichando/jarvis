// @vitest-environment node
import { beforeAll, describe, expect, it } from 'vitest'
import type { NextRequest } from 'next/server'
import { NextRequest as NextRequestCtor } from 'next/server'

// Online hardened config, served at a real domain.
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

describe('proxy: CCR /v1/sessions GET reachable with a per-user token (teleport machinery)', () => {
  it('waives the shared-token gate for the read paths on GET', async () => {
    for (const path of [
      '/api/v1/sessions',
      '/api/v1/sessions/abc123',
      '/api/v1/sessions/abc123/events',
    ]) {
      expect(await authRequired(proxy(req('GET', path)))).toBe(false)
    }
  })

  it('still gates MUTATIONS on the same paths (only the route/shared-token protects those)', async () => {
    // A non-browser POST/PATCH with a per-user token must NOT bypass — it's not
    // a browser same-origin write and it isn't the shared token.
    expect(await authRequired(proxy(req('POST', '/api/v1/sessions')))).toBe(true)
    expect(await authRequired(proxy(req('PATCH', '/api/v1/sessions/abc123')))).toBe(true)
    expect(await authRequired(proxy(req('POST', '/api/v1/sessions/abc123/events')))).toBe(true)
  })

  it('does not over-match a deeper/other path', async () => {
    expect(await authRequired(proxy(req('GET', '/api/v1/sessions/abc/events/extra')))).toBe(true)
    expect(await authRequired(proxy(req('GET', '/api/v1/admin/enqueue')))).toBe(true)
  })
})
