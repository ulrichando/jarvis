import { NextResponse } from 'next/server'
import { extractBearer } from '@/lib/bridge/auth'
import { getStore } from '@/lib/bridge/db'
import { validateEnvSecret } from '@/lib/bridge/store'
import { bridgeError } from '@/lib/bridge/errors'

export async function POST(
  req: Request,
  ctx: { params: Promise<{ envId: string }> },
): Promise<NextResponse> {
  const { envId } = await ctx.params
  const token = extractBearer(req.headers.get('authorization'))
  if (!token) return bridgeError(401, 'unauthorized', 'Missing bearer')
  try {
    const store = getStore()
    if (!validateEnvSecret(store, envId, token)) {
      return bridgeError(401, 'unauthorized', 'Invalid environment_secret')
    }
    // Bump last_seen_at on the environment row. The spec also asks for a
    // matching bump on the session row, but the `sessions` schema has no
    // last_seen_at column today (sessions are created on archive only).
    // Sub-project 3 will add the column + bump it here when the session
    // lifecycle is fleshed out.
    store.db
      .prepare('UPDATE environments SET last_seen_at = ? WHERE environment_id = ?')
      .run(Date.now(), envId)
    return new NextResponse(null, { status: 204 })
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    return bridgeError(500, 'internal_error', `DB error: ${msg}`)
  }
}
