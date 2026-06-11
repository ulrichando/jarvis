import { NextResponse } from 'next/server'
import { getStore } from '@/lib/bridge/db'
import {
  listSessions,
  listSessionEvents,
  findEnvironment,
  type EnvironmentRow,
} from '@/lib/bridge/store'
import { bridgeError } from '@/lib/bridge/errors'

function repoLabel(env: EnvironmentRow | null): string | null {
  if (!env) return null
  if (env.git_repo_url) {
    const s = env.git_repo_url.replace(/\.git$/, '').split('/')
    return s.slice(-2).join('/') || (s.slice(-1)[0] ?? null)
  }
  return env.directory.split('/').filter(Boolean).slice(-1)[0] ?? null
}

// GET /api/bridge/v1/sessions — sessions for the /code main view, each with a
// title (first user prompt), a preview (latest event), repo + machine, and a
// derived status. Newest first, capped.
export async function GET(): Promise<NextResponse> {
  try {
    const store = getStore()
    const sessions = listSessions(store)
      .slice(0, 40)
      .map((s) => {
        const events = listSessionEvents(store, s.session_id, 0)
        const first = events.find((e) => e.type === 'user_prompt')
        const last = events[events.length - 1]
        const env = s.environment_id ? findEnvironment(store, s.environment_id) : null
        const safe = (json: string | undefined, key: string): string => {
          try {
            const v = (JSON.parse(json ?? '{}') as Record<string, unknown>)[key]
            return typeof v === 'string' ? v : ''
          } catch {
            return ''
          }
        }
        const title = safe(first?.payload_json, 'prompt') || 'Session'
        const preview =
          safe(last?.payload_json, 'text') ||
          safe(last?.payload_json, 'status') ||
          safe(last?.payload_json, 'message')
        const status = s.archived
          ? 'done'
          : last && last.type !== 'user_prompt'
            ? 'working'
            : 'needs_input'
        return {
          session_id: s.session_id,
          title: title.slice(0, 90),
          preview: preview.slice(0, 110),
          repo: repoLabel(env),
          machine_name: env?.machine_name ?? null,
          created_at: s.created_at,
          status,
        }
      })
    return NextResponse.json({ sessions })
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    return bridgeError(500, 'internal_error', `DB error: ${msg}`)
  }
}
