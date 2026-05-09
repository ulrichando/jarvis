import { NextResponse } from 'next/server'
import { extractBearer } from '@/lib/bridge/auth'
import { getStore } from '@/lib/bridge/db'
import { findEnvironment } from '@/lib/bridge/store'
import { bridgeError } from '@/lib/bridge/errors'

export async function POST(
  req: Request,
  ctx: { params: Promise<{ envId: string; workId: string }> },
): Promise<NextResponse> {
  const { envId } = await ctx.params
  const token = extractBearer(req.headers.get('authorization'))
  if (!token) return bridgeError(401, 'unauthorized', 'Missing bearer')
  try {
    const env = findEnvironment(getStore(), envId)
    if (!env || env.environment_secret !== token) {
      return bridgeError(401, 'unauthorized', 'Invalid environment_secret')
    }
    // Ack is a no-op on the server side beyond auth — the lease was already
    // taken by /work/poll. Just return 204 to confirm the CLI is alive.
    return new NextResponse(null, { status: 204 })
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    return bridgeError(500, 'internal_error', `DB error: ${msg}`)
  }
}
