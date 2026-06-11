import { NextResponse } from 'next/server'
import { getStore } from '@/lib/bridge/db'
import { listEnvironments } from '@/lib/bridge/store'
import { bridgeError } from '@/lib/bridge/errors'

// GET /api/bridge/v1/environments — list registered machines (workers) for the
// /code machine picker. Unauthenticated like the other v1 routes (relies on the
// 127.0.0.1 loopback bind). NEVER exposes environment_secret to the UI.
export async function GET(): Promise<NextResponse> {
  try {
    const store = getStore()
    const environments = listEnvironments(store).map((e) => ({
      environment_id: e.environment_id,
      machine_name: e.machine_name,
      directory: e.directory,
      branch: e.branch,
      git_repo_url: e.git_repo_url,
      max_sessions: e.max_sessions,
      worker_type: e.worker_type,
      created_at: e.created_at,
      last_seen_at: e.last_seen_at,
    }))
    return NextResponse.json({ environments })
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    return bridgeError(500, 'internal_error', `DB error: ${msg}`)
  }
}
