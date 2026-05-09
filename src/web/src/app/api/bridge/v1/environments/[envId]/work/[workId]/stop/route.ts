import { NextResponse } from 'next/server'
import { extractBearer } from '@/lib/bridge/auth'
import { getStore } from '@/lib/bridge/db'
import { findEnvironment, stopWork } from '@/lib/bridge/store'
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
    const env = findEnvironment(store, envId)
    if (!env || env.environment_secret !== token) {
      return bridgeError(401, 'unauthorized', 'Invalid environment_secret')
    }
    stopWork(store, envId, workId)
    return new NextResponse(null, { status: 204 })
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    return bridgeError(500, 'internal_error', `DB error: ${msg}`)
  }
}
