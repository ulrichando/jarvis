import { NextResponse } from 'next/server'
import { getStore } from '@/lib/bridge/db'
import { listEnvironments, reapStaleSandboxes, isEnvironmentOnline, ensureDefaultCloudEnv, BOT_ENV_MACHINE } from '@/lib/bridge/store'
import { getUserIdOrSharedLocal } from '@/lib/auth-helpers'
import { bridgeError } from '@/lib/bridge/errors'

// GET /api/bridge/v1/environments — the logged-in user's registered machines
// (workers) for the /code machine picker. Per-user scoped: only environments
// the CLI registered under this user's token are returned. NEVER exposes
// environment_secret to the UI.
export async function GET(req: Request): Promise<NextResponse> {
  try {
    const store = getStore()
    const userId = await getUserIdOrSharedLocal(req.headers)
    if (!userId) return bridgeError(401, 'unauthenticated', 'Sign in required')
    ensureDefaultCloudEnv(store, userId) // always offer a "Default" cloud env (claude.ai parity)
    reapStaleSandboxes(store) // lazy GC of stale cloud sandboxes
    const now = Date.now()
    const environments = listEnvironments(store, userId)
      // Hide the gh-app bot's dedicated env from the picker — it's owned by the
      // user (so its sessions stay watchable in the sidebar) but is not a
      // user-selectable environment. Matches claude.ai/code.
      .filter((e) => e.machine_name !== BOT_ENV_MACHINE)
      .map((e) => ({
      environment_id: e.environment_id,
      machine_name: e.machine_name,
      directory: e.directory,
      branch: e.branch,
      git_repo_url: e.git_repo_url,
      max_sessions: e.max_sessions,
      worker_type: e.worker_type,
      created_at: e.created_at,
      last_seen_at: e.last_seen_at,
      online: isEnvironmentOnline(e, now),
    }))
    return NextResponse.json({ environments })
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    return bridgeError(500, 'internal_error', `DB error: ${msg}`)
  }
}
