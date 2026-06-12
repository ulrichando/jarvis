import { NextResponse } from 'next/server'
import { extractBearer } from '@/lib/bridge/auth'
import { getStore } from '@/lib/bridge/db'
import {
  findEnvironment,
  resolveBridgeToken,
  stopWork,
  validateEnvSecret,
  validateWorkSessionToken,
} from '@/lib/bridge/store'
import { bridgeError } from '@/lib/bridge/errors'

export async function POST(
  req: Request,
  ctx: { params: Promise<{ envId: string; workId: string }> },
): Promise<NextResponse> {
  const { envId, workId } = await ctx.params
  const token = extractBearer(req.headers.get('authorization'))
  if (!token) return bridgeError(401, 'unauthorized', 'Missing bearer')
  try {
    const store = getStore()
    // The CLI's stopWork sends its login credential (the jbr_ bridge token
    // in self-hosted setups; bridgeApi wraps it in withOAuthRetry), not the
    // environment secret — e.g. when dropping work whose secret failed to
    // decode, or work for a foreign session. Accept the env secret, an
    // owner-matched bridge token, or the work's own session ingress token.
    const tokenUser = resolveBridgeToken(store, token)
    const env = findEnvironment(store, envId)
    const bridgeTokenOk =
      tokenUser !== null && (!env?.user_id || env.user_id === tokenUser)
    if (
      !validateEnvSecret(store, envId, token) &&
      !bridgeTokenOk &&
      !validateWorkSessionToken(store, envId, workId, token)
    ) {
      return bridgeError(401, 'unauthorized', 'Invalid credential')
    }
    stopWork(store, envId, workId)
    return new NextResponse(null, { status: 204 })
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    return bridgeError(500, 'internal_error', `DB error: ${msg}`)
  }
}
