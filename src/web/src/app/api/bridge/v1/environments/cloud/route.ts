import { NextResponse } from 'next/server'
import { getStore } from '@/lib/bridge/db'
import { createEnvironment, listEnvironments } from '@/lib/bridge/store'
import { getUserId } from '@/lib/auth-helpers'
import { validRepoFullName } from '@/lib/bridge/containers'
import { githubStatus } from '@/lib/connectors/github'
import { bridgeError } from '@/lib/bridge/errors'

// POST /api/bridge/v1/environments/cloud — register a cloud-container target
// for a GitHub repo. It's a normal environment row with worker_type
// 'container', so it shows up in the /code machine picker and dispatches
// through the existing tasks flow with zero picker changes; the tasks route
// branches on worker_type to launch a docker container instead of enqueueing
// bridge work. Idempotent per (user, repo).
export async function POST(req: Request): Promise<NextResponse> {
  const body = (await req.json().catch(() => null)) as {
    repo?: string
  } | null
  const repo = typeof body?.repo === 'string' ? body.repo.trim() : ''
  if (!repo || !validRepoFullName(repo)) {
    return bridgeError(400, 'invalid_request', 'repo must be "owner/name"')
  }
  try {
    const store = getStore()
    const userId = await getUserId(req.headers)
    const repoUrl = `https://github.com/${repo}`
    const existing = listEnvironments(store, userId).find(
      (e) => e.worker_type === 'container' && e.git_repo_url === repoUrl,
    )
    if (existing) {
      return NextResponse.json(
        { environment_id: existing.environment_id, reused: true },
        { status: 200 },
      )
    }
    const gh = await githubStatus()
    const env = createEnvironment(store, {
      machine_name: 'Cloud container',
      directory: '/workspace',
      git_repo_url: repoUrl,
      max_sessions: 4,
      worker_type: 'container',
      user_id: userId,
    })
    return NextResponse.json(
      {
        environment_id: env.environment_id,
        reused: false,
        // Surface a setup hint the UI can show: private repos won't clone
        // without the GitHub connector.
        github_connected: gh.connected,
      },
      { status: 201 },
    )
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    return bridgeError(500, 'internal_error', `DB error: ${msg}`)
  }
}
