import { NextResponse } from 'next/server'
import { randomBytes, randomUUID } from 'node:crypto'
import { getStore } from '@/lib/bridge/db'
import {
  appendInbound,
  appendSessionEvent,
  createEnvironment,
  findEnvironment,
  getOrCreateSession,
  listEnvironments,
  listSessionEvents,
  listSessions,
  resolveBridgeToken,
} from '@/lib/bridge/store'
import { launchContainerSession, validRepoFullName } from '@/lib/bridge/containers'
import { extractBearer } from '@/lib/bridge/auth'
import { bridgeError } from '@/lib/bridge/errors'

export const runtime = 'nodejs'

// CLI-facing /code session API (the `jarvis cloud` / `jarvis teleport` pair —
// self-hosted equivalent of claude.ai's `--cloud`/`--teleport`). Unlike the
// cookie-authed web routes, both verbs take the Remote Control bearer token
// minted by `jarvis auth login` (same strict gate as /api/bridge/v1/keys).
//
//   GET  → recent container sessions (id/title/repo/updated) for the
//          `jarvis teleport` no-arg picker.
//   POST → create a session from the terminal: ensure the per-(user,repo)
//          container environment (same idempotent row the web picker makes),
//          seed the prompt, launch the container. {repo, prompt, model?,
//          permission_mode?} → {session_id, url}.

function authedUser(req: Request): string | null {
  const token = extractBearer(req.headers.get('authorization'))
  return token ? resolveBridgeToken(getStore(), token) : null
}

export async function GET(req: Request): Promise<NextResponse> {
  const userId = authedUser(req)
  if (!userId) return bridgeError(401, 'unauthorized', 'A valid bridge token is required')
  try {
    const store = getStore()
    const rows = listSessions(store, userId)
      .slice(0, 60)
      .map((s) => {
        const env = s.environment_id ? findEnvironment(store, s.environment_id) : null
        if (env?.worker_type !== 'container') return null
        const repo = (env.git_repo_url ?? '')
          .replace(/^https:\/\/github\.com\//, '')
          .replace(/\.git$/, '')
        const events = listSessionEvents(store, s.session_id, 0)
        const first = events.find((e) => e.type === 'user_prompt')
        let title = s.title ?? ''
        if (!title && first) {
          try {
            const p = JSON.parse(first.payload_json) as { prompt?: string }
            title = typeof p.prompt === 'string' ? p.prompt : ''
          } catch {
            /* unparseable seed event — leave untitled */
          }
        }
        return {
          session_id: s.session_id,
          title: title.slice(0, 120) || 'Session',
          repo,
          archived: !!s.archived,
          updated_at: events[events.length - 1]?.created_at ?? null,
        }
      })
      .filter((r): r is NonNullable<typeof r> => r !== null && !r.archived)
      .slice(0, 25)
    return NextResponse.json({ sessions: rows })
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    return bridgeError(500, 'internal_error', `list failed: ${msg}`)
  }
}

export async function POST(req: Request): Promise<NextResponse> {
  const userId = authedUser(req)
  if (!userId) return bridgeError(401, 'unauthorized', 'A valid bridge token is required')
  const body = (await req.json().catch(() => null)) as {
    repo?: string
    prompt?: string
    model?: string
    permission_mode?: string
  } | null
  const repo = typeof body?.repo === 'string' ? body.repo.trim() : ''
  const prompt = typeof body?.prompt === 'string' ? body.prompt.trim() : ''
  if (!repo || !validRepoFullName(repo) || !prompt) {
    return bridgeError(400, 'invalid_request', 'repo ("owner/name") and a non-empty prompt are required')
  }
  try {
    const store = getStore()
    // Same idempotent per-(user,repo) container target the web picker creates.
    const repoUrl = `https://github.com/${repo}`
    const env =
      listEnvironments(store, userId).find(
        (e) => e.worker_type === 'container' && e.git_repo_url === repoUrl,
      ) ??
      createEnvironment(store, {
        machine_name: 'Cloud container',
        directory: '/workspace',
        git_repo_url: repoUrl,
        max_sessions: 4,
        worker_type: 'container',
        user_id: userId,
      })

    // Mirror the tasks route's container branch: create → surface prompt →
    // seed mode/model → seed the prompt inbound → launch (async; init steps
    // stream into the session as status events).
    const sessionId = randomBytes(8).toString('hex')
    getOrCreateSession(store, sessionId, env.environment_id)
    const uuid = randomUUID()
    appendSessionEvent(store, sessionId, {
      type: 'user_prompt',
      payload: { type: 'user_prompt', prompt, uuid },
    })
    const VALID_MODES = ['default', 'acceptEdits', 'plan', 'bypassPermissions', 'dontAsk']
    if (typeof body?.permission_mode === 'string' && VALID_MODES.includes(body.permission_mode)) {
      const modeUuid = randomUUID()
      appendInbound(store, sessionId, {
        type: 'control_request',
        uuid: modeUuid,
        request_id: modeUuid,
        request: { subtype: 'set_permission_mode', mode: body.permission_mode },
      })
    }
    if (typeof body?.model === 'string' && body.model.trim()) {
      const modelUuid = randomUUID()
      appendInbound(store, sessionId, {
        type: 'control_request',
        uuid: modelUuid,
        request_id: modelUuid,
        request: { subtype: 'set_model', model: body.model.trim() },
      })
    }
    appendInbound(store, sessionId, {
      type: 'user',
      uuid,
      session_id: sessionId,
      parent_tool_use_id: null,
      message: { role: 'user', content: [{ type: 'text', text: prompt }] },
    })
    const origin = new URL(req.url).origin
    void launchContainerSession(store, {
      sessionId,
      repoFullName: repo,
      baseUrl: origin,
      model: typeof body?.model === 'string' && body.model.trim() ? body.model.trim() : undefined,
    }).catch(() => {
      /* failure already emitted as a ✗ status event + container reaped */
    })
    return NextResponse.json(
      { session_id: sessionId, url: `${origin}/code/session_${sessionId}` },
      { status: 200 },
    )
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    return bridgeError(500, 'internal_error', `create failed: ${msg}`)
  }
}
