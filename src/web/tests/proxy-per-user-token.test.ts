// @vitest-environment node
import { beforeAll, describe, expect, it } from 'vitest'
import type { NextRequest } from 'next/server'
import { NextRequest as NextRequestCtor } from 'next/server'

// Proxy reads env at module load — pin the ONLINE hardened config (shared-token
// gate ON, served at a real domain) BEFORE importing it.
process.env.JARVIS_REQUIRE_LOCAL_AUTH = '1'
process.env.JARVIS_LOCAL_API_TOKEN = 'shared-infra-secret'
process.env.JARVIS_WEB_ALLOWED_HOSTS = '0wlan.com'
delete process.env.JARVIS_AUTH_DISABLED

let proxy: (req: NextRequest) => Response
beforeAll(async () => {
  ;({ proxy } = (await import('@/proxy')) as unknown as { proxy: (req: NextRequest) => Response })
})

// A non-browser (CLI) request to the online host with a per-user bridge token —
// no session cookie, no Sec-Fetch-Site.
function cliReq(method: string, path: string, bearer: string): NextRequest {
  return new NextRequestCtor(`https://0wlan.com${path}`, {
    method,
    headers: { host: '0wlan.com', authorization: `Bearer ${bearer}` },
  })
}

async function isAuthRequired(res: Response): Promise<boolean> {
  if (res.status !== 401) return false
  const body = await res.clone().json().catch(() => null)
  return (body as { error?: string } | null)?.error === 'auth required'
}

describe('proxy: per-user bridge tokens on self-authenticating routes (online)', () => {
  const perUserToken = 'jbr_some_per_user_token'

  it('waives the shared-token gate for keys / cli-sessions / teleport → reaches the handler', async () => {
    for (const path of [
      '/api/bridge/v1/keys',
      '/api/bridge/v1/cli/sessions',
      '/api/bridge/v1/sessions/abc123def456/teleport',
    ]) {
      const res = await proxy(cliReq('GET', path, perUserToken))
      // Not the proxy's shared-token rejection — the route now gets to
      // self-authenticate the per-user token (resolveBridgeToken + ownership).
      expect(await isAuthRequired(res)).toBe(false)
    }
  })

  it('STILL gates a non-self-authenticating route (admin/enqueue) — a per-user token cannot reach it', async () => {
    const res = await proxy(cliReq('POST', '/api/bridge/v1/admin/enqueue', perUserToken))
    expect(await isAuthRequired(res)).toBe(true)
  })

  it('does not over-match — a teleport-suffixed but different path stays gated', async () => {
    const res = await proxy(cliReq('GET', '/api/bridge/v1/sessions/abc/teleport/extra', perUserToken))
    expect(await isAuthRequired(res)).toBe(true)
  })

  it('the shared infra token still passes everywhere (unchanged behavior)', async () => {
    const res = await proxy(cliReq('POST', '/api/bridge/v1/admin/enqueue', 'shared-infra-secret'))
    expect(await isAuthRequired(res)).toBe(false)
  })
})
