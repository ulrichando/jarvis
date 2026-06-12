import { NextResponse } from 'next/server'
import { extractBearer } from '@/lib/bridge/auth'
import { getStore } from '@/lib/bridge/db'
import {
  validateEnvSecret,
  validateWorkSessionToken,
  heartbeatWork,
} from '@/lib/bridge/store'
import { bridgeError } from '@/lib/bridge/errors'

const LEASE_TTL_MS = 60_000

export async function POST(
  req: Request,
  ctx: { params: Promise<{ envId: string; workId: string }> },
): Promise<NextResponse> {
  const { envId, workId } = await ctx.params
  const token = extractBearer(req.headers.get('authorization'))
  if (!token) return bridgeError(401, 'unauthorized', 'Missing bearer')
  try {
    const store = getStore()
    // The CLI heartbeats active session work with the work secret's
    // session_ingress_token (bridgeApi.heartbeatWork), not the environment
    // secret — accept either, like /ack. Without this every heartbeat
    // 401'd, leases silently expired, and session work was re-delivered
    // every ~60s (transport churn on the REPL, log noise on the daemon).
    if (
      !validateEnvSecret(store, envId, token) &&
      !validateWorkSessionToken(store, envId, workId, token)
    ) {
      return bridgeError(401, 'unauthorized', 'Invalid credential')
    }
    const result = heartbeatWork(store, envId, workId, LEASE_TTL_MS)
    return NextResponse.json(
      {
        lease_extended: result.lease_extended,
        state: result.state,
        // last_heartbeat is generated AFTER heartbeatWork's DB write —
        // it reflects the wall-clock at response-formation time, NOT the
        // exact epoch persisted to lease_expires_at. The spec only
        // requires an ISO-8601 timestamp here; consumers must not
        // reverse-derive lease_expires_at from this field.
        last_heartbeat: new Date().toISOString(),
        ttl_seconds: result.ttl_seconds,
      },
      { status: 200 },
    )
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    return bridgeError(500, 'internal_error', `DB error: ${msg}`)
  }
}
