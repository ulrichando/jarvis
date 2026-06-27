import { NextResponse } from 'next/server'
import { getStore } from '@/lib/bridge/db'
import {
  isEnvironmentOnline,
  listEnvironments,
  resolveBridgeToken,
} from '@/lib/bridge/store'
import { extractBearer } from '@/lib/bridge/auth'

// CCR-compat (Phase B): the JARVIS-web backend that the intact teleport /
// ultraplan CLI client talks to instead of Anthropic's cloud. Mounted at
// /api/v1/* — the client's BASE_API_URL is set to "<web-origin>/api" so its
// `${BASE_API_URL}/v1/...` requests land here. Reuses src/lib/bridge/store.ts.
// Spec: docs/superpowers/specs/2026-06-27-jarvis-web-ccr-backend-design.md

// GET /api/v1/environment_providers — list the environments the client may
// target. Maps JARVIS bridge environments to the CCR environment shape. The
// client picks one and creates a session against it; a registered, online
// bridge worker (bin/jarvis --feature=BRIDGE_MODE) then leases + runs it.
export async function GET(req: Request): Promise<NextResponse> {
  const store = getStore()
  const token = extractBearer(req.headers.get('authorization'))
  const userId = token ? resolveBridgeToken(store, token) : null
  const envs = listEnvironments(store, userId ?? undefined)
  const environments = envs.map((e) => ({
    kind: e.worker_type === 'container' ? 'anthropic_cloud' : 'bridge',
    environment_id: e.environment_id,
    name: e.machine_name,
    created_at: new Date(e.created_at).toISOString(),
    state: 'active' as const,
    online: isEnvironmentOnline(e),
  }))
  return NextResponse.json({
    environments,
    has_more: false,
    first_id: environments[0]?.environment_id ?? null,
    last_id: environments[environments.length - 1]?.environment_id ?? null,
  })
}
