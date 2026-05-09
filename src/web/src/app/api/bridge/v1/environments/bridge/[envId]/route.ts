import { NextResponse } from 'next/server'
import { getStore } from '@/lib/bridge/db'
import { deleteEnvironment, findEnvironment } from '@/lib/bridge/store'
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
    // against envId enumeration. Both "no env" and "wrong secret" return
    // the same 401 — the owner with a valid secret gets the real 204.
    if (!env || env.environment_secret !== token) {
      return bridgeError(401, 'unauthorized', 'Invalid environment_secret')
    }
    deleteEnvironment(store, envId)
    return new NextResponse(null, { status: 204 })
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    return bridgeError(500, 'internal_error', `DB error: ${msg}`)
  }
}
