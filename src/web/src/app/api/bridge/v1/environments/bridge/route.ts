import { NextResponse } from 'next/server'
import { getStore } from '@/lib/bridge/db'
import { createEnvironment, resolveBridgeToken } from '@/lib/bridge/store'
import { extractBearer } from '@/lib/bridge/auth'
import { LOCAL_USER_ID } from '@/lib/chat/persist'
import { bridgeError } from '@/lib/bridge/errors'

export async function POST(req: Request): Promise<NextResponse> {
  const body = (await req.json().catch(() => null)) as {
    machine_name?: string
    directory?: string
    branch?: string
    git_repo_url?: string
    max_sessions?: number
    metadata?: { worker_type?: string }
    environment_id?: string
  } | null
  if (
    !body ||
    typeof body.machine_name !== 'string' ||
    typeof body.directory !== 'string' ||
    typeof body.max_sessions !== 'number'
  ) {
    return bridgeError(400, 'invalid_request', 'Missing required fields')
  }
  try {
    const store = getStore()
    // Per-user ownership: the CLI sends its long-lived JARVIS token as the
    // register bearer. Resolve it to the owning user; tokenless/anonymous
    // registers (auth-disabled, thin worker) default to the local user.
    const token = extractBearer(req.headers.get('authorization'))
    const userId = (token && resolveBridgeToken(store, token)) || LOCAL_USER_ID
    const result = createEnvironment(store, {
      machine_name: body.machine_name,
      directory: body.directory,
      branch: body.branch,
      git_repo_url: body.git_repo_url,
      max_sessions: body.max_sessions,
      worker_type: body.metadata?.worker_type ?? 'jarvis',
      reuse_id: body.environment_id,
      user_id: userId,
    })
    return NextResponse.json(result, { status: 200 })
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    return bridgeError(500, 'internal_error', `DB error: ${msg}`)
  }
}
