import { NextResponse } from 'next/server'
import { getStore } from '@/lib/bridge/db'
import {
  leaseNextWork,
  reclaimExpiredLeases,
  validateEnvSecret,
} from '@/lib/bridge/store'
import { extractBearer } from '@/lib/bridge/auth'
import { waitForWork } from '@/lib/bridge/events'
import { bridgeError } from '@/lib/bridge/errors'

const LEASE_TTL_MS = 60_000
// MUST stay under the CLI's poll budget: bridgeApi.pollForWork uses
// axios timeout 10_000. The original 25s (claude.ai-style long-poll) made
// every idle poll time out CLIENT-side — the REPL bridge read that as a
// poll failure and sat in "Remote Control reconnecting…" forever.
const DEFAULT_POLL_TIMEOUT_MS = 8_000

function pollTimeoutMs(): number {
  const env = process.env.BRIDGE_POLL_TIMEOUT_MS
  if (!env) return DEFAULT_POLL_TIMEOUT_MS
  const n = parseInt(env, 10)
  return Number.isFinite(n) && n > 0 ? n : DEFAULT_POLL_TIMEOUT_MS
}

export async function GET(
  req: Request,
  ctx: { params: Promise<{ envId: string }> },
): Promise<NextResponse> {
  const { envId } = await ctx.params
  const token = extractBearer(req.headers.get('authorization'))
  if (!token) return bridgeError(401, 'unauthorized', 'Missing bearer token')
  const store = getStore()
  if (!validateEnvSecret(store, envId, token)) {
    return bridgeError(401, 'unauthorized', 'Invalid environment_secret')
  }

  try {
    const url = new URL(req.url)
    const reclaimRaw = url.searchParams.get('reclaim_older_than_ms')
    if (reclaimRaw !== null) {
      const cutoffMs = parseInt(reclaimRaw, 10)
      if (Number.isFinite(cutoffMs) && cutoffMs >= 0) {
        reclaimExpiredLeases(store, envId, cutoffMs)
      }
    }

    const tryLease = (): NextResponse | null => {
      const work = leaseNextWork(store, envId, LEASE_TTL_MS)
      if (!work) return null
      return NextResponse.json(
        {
          id: work.id,
          type: 'work',
          environment_id: work.environment_id,
          state: work.state,
          data: work.data,
          secret: work.secret_b64url,
          created_at: new Date(work.created_at).toISOString(),
        },
        { status: 200 },
      )
    }

    const immediate = tryLease()
    if (immediate) return immediate

    const woke = await waitForWork(envId, pollTimeoutMs())
    if (woke) {
      const after = tryLease()
      if (after) return after
    }
    return NextResponse.json(null, { status: 200 })
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    return bridgeError(500, 'internal_error', `DB error: ${msg}`)
  }
}
