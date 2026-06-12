import { NextResponse } from 'next/server'
import { extractBearer } from '@/lib/bridge/auth'
import { getStore } from '@/lib/bridge/db'
import { validateEnvSecret, validateWorkSessionToken } from '@/lib/bridge/store'
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
    // The CLI acks with the work secret's session_ingress_token
    // (bridgeApi.acknowledgeWork), NOT the environment secret — accept
    // either. Rejecting the session token here made every ack 401, which
    // was invisible (acks are best-effort) until the unrenewed lease
    // expired and the server re-delivered the same session work forever.
    if (
      !validateEnvSecret(store, envId, token) &&
      !validateWorkSessionToken(store, envId, workId, token)
    ) {
      return bridgeError(401, 'unauthorized', 'Invalid credential')
    }
    // Ack is a no-op on the server side beyond auth — the lease was already
    // taken by /work/poll. Just return 204 to confirm the CLI is alive.
    return new NextResponse(null, { status: 204 })
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    return bridgeError(500, 'internal_error', `DB error: ${msg}`)
  }
}
