import { NextResponse } from 'next/server'
import { getStore } from '@/lib/bridge/db'
import {
  deleteEnvironment,
  findEnvironment,
  resolveBridgeToken,
} from '@/lib/bridge/store'
import { extractBearer } from '@/lib/bridge/auth'
import { bridgeError } from '@/lib/bridge/errors'

export async function DELETE(
  req: Request,
  ctx: { params: Promise<{ envId: string }> },
): Promise<NextResponse> {
  const { envId } = await ctx.params
  const token = extractBearer(req.headers.get('authorization'))
  if (!token) {
    return bridgeError(401, 'unauthorized', 'Missing bearer token')
  }
  try {
    const store = getStore()
    const env = findEnvironment(store, envId)
    // Single lookup; check auth before revealing existence to defend
    // against envId enumeration. Both "no env" and "wrong credential" return
    // the same 401. Accepted credentials: the environment's secret, OR the
    // owning user's bridge token — the CLI's deregister path (bridgeApi
    // resolveAuth) sends the per-user token, not the env secret, so
    // secret-only auth made every worker deregister 401 (leaking dead
    // environment rows on the server).
    const tokenUser = resolveBridgeToken(store, token)
    const okSecret = !!env && env.environment_secret === token
    // Explicit owner match only — ownerless envs are deletable solely via
    // their environment_secret (IDOR guard, mirrors the create route).
    const okOwner =
      !!env && !!tokenUser && !!env.user_id && tokenUser === env.user_id
    if (!okSecret && !okOwner) {
      return bridgeError(401, 'unauthorized', 'Invalid environment_secret')
    }
    deleteEnvironment(store, envId)
    return new NextResponse(null, { status: 204 })
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    return bridgeError(500, 'internal_error', `DB error: ${msg}`)
  }
}
