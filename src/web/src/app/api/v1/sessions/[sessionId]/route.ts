import { NextResponse } from 'next/server'
import { getStore } from '@/lib/bridge/db'
import {
  findSession,
  setSessionTitle,
  resolveBridgeToken,
  findEnvironment,
  type Store,
} from '@/lib/bridge/store'
import { extractBearer } from '@/lib/bridge/auth'
import { bridgeError } from '@/lib/bridge/errors'
import { ccrSessionStatus } from '@/lib/bridge/ccrCompat'
import { getContainerDiff } from '@/lib/bridge/containers'

// CCR-compat single session — the client's fetchSession (metadata: status +
// branch) and PATCH (retitle). See ../environment_providers/route.ts header.

// Per-user auth: resolve the bridge token and verify it owns the session's
// environment. Lets these routes be reached online with a PER-USER token
// (teleport) rather than only the shared proxy token.
export function authSessionOwner(
  store: Store,
  req: Request,
  sessionId: string,
): { userId: string } | { deny: NextResponse } {
  const token = extractBearer(req.headers.get('authorization'))
  const userId = token ? resolveBridgeToken(store, token) : null
  if (!userId) return { deny: bridgeError(401, 'unauthorized', 'A valid bridge token is required') }
  const s = findSession(store, sessionId)
  if (!s) return { deny: bridgeError(404, 'not_found', 'Session not found') }
  const env = s.environment_id ? findEnvironment(store, s.environment_id) : null
  if (env?.user_id && env.user_id !== userId) {
    return { deny: bridgeError(403, 'forbidden', 'Not your session') }
  }
  return { userId }
}

export async function GET(
  req: Request,
  ctx: { params: Promise<{ sessionId: string }> },
): Promise<NextResponse> {
  const { sessionId } = await ctx.params
  const store = getStore()
  const auth = authSessionOwner(store, req, sessionId)
  if ('deny' in auth) return auth.deny
  const s = findSession(store, sessionId)
  if (!s) return bridgeError(404, 'not_found', 'Session not found')
  // session_context is what the teleport client reads: sources[].url →
  // the repo (validateSessionRepository), outcomes[].git_info.branches[0] →
  // the branch (getBranchFromSession). Repo from the env; branch from the live
  // diff, else the session-branch convention (matches the /teleport route).
  const env = s.environment_id ? findEnvironment(store, s.environment_id) : null
  const repoUrl = env?.git_repo_url || null
  const diff = await getContainerDiff(store, sessionId)
  const branch =
    'error' in diff || !diff.branch || diff.branch === 'HEAD'
      ? `jarvis/session-${sessionId.slice(0, 8)}`
      : diff.branch
  return NextResponse.json({
    type: 'session',
    id: s.session_id,
    title: s.title,
    session_status: ccrSessionStatus(s),
    environment_id: s.environment_id,
    created_at: new Date(s.created_at).toISOString(),
    updated_at: new Date(s.created_at).toISOString(),
    session_context: {
      sources: repoUrl ? [{ type: 'git_repository' as const, url: repoUrl }] : [],
      outcomes: [{ type: 'git_repository' as const, git_info: { branches: [branch] } }],
    },
  })
}

export async function PATCH(
  req: Request,
  ctx: { params: Promise<{ sessionId: string }> },
): Promise<NextResponse> {
  const { sessionId } = await ctx.params
  const token = extractBearer(req.headers.get('authorization'))
  if (!token) return bridgeError(401, 'unauthorized', 'Missing bearer')
  const body = (await req.json().catch(() => null)) as { title?: string } | null
  const store = getStore()
  const s = findSession(store, sessionId)
  if (!s) return bridgeError(404, 'not_found', 'Session not found')
  if (typeof body?.title === 'string') setSessionTitle(store, sessionId, body.title)
  const updated = findSession(store, sessionId)
  return NextResponse.json({
    type: 'session',
    id: sessionId,
    title: updated?.title ?? null,
    session_status: ccrSessionStatus(updated),
    environment_id: updated?.environment_id ?? null,
  })
}
