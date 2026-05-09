import { NextResponse } from 'next/server'
import { getStore } from '@/lib/bridge/db'
import { createEnvironment } from '@/lib/bridge/store'
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
  const store = getStore()
  const result = createEnvironment(store, {
    machine_name: body.machine_name,
    directory: body.directory,
    branch: body.branch,
    git_repo_url: body.git_repo_url,
    max_sessions: body.max_sessions,
    worker_type: body.metadata?.worker_type ?? 'jarvis',
    reuse_id: body.environment_id,
  })
  return NextResponse.json(result, { status: 200 })
}
