import { getStore } from '@/lib/bridge/db'
import {
  findSession,
  validateGitCapToken,
  getSessionGitScope,
  appendSessionEvent,
} from '@/lib/bridge/store'
import { getGithubToken } from '@/lib/connectors/github'
import { parseGitRequest, assertRepoAllowed, forwardToGithub } from '@/lib/bridge/git-proxy'
import { bridgeError } from '@/lib/bridge/errors'

// Scoped-credential git proxy for /code container sessions. In-container git
// authenticates here with a per-session capability token (the Basic-auth
// password); this route validates it, enforces the session's repo scope +
// operation, then injects the REAL GitHub PAT host-side and forwards to
// github.com. The real token never enters the container.
// Spec: docs/superpowers/specs/2026-06-19-scoped-credential-git-proxy-design.md

type Ctx = { params: Promise<{ sessionId: string; path: string[] }> }

/** The cap token is the Basic-auth password git sends (username ignored). */
function capTokenFrom(req: Request): string | null {
  const h = req.headers.get('authorization')
  const m = h && /^Basic\s+(.+)$/i.exec(h)
  if (!m) return null
  try {
    const dec = Buffer.from(m[1], 'base64').toString('utf8')
    const i = dec.indexOf(':')
    return i >= 0 ? dec.slice(i + 1) : dec
  } catch {
    return null
  }
}

async function handle(req: Request, ctx: Ctx): Promise<Response> {
  const { sessionId, path } = await ctx.params
  const store = getStore()
  const cap = capTokenFrom(req)
  if (!cap || !validateGitCapToken(store, sessionId, cap)) {
    return bridgeError(401, 'unauthorized', 'Invalid git credential')
  }
  const target = parseGitRequest(path, new URL(req.url).searchParams)
  if (!target) return bridgeError(400, 'invalid_request', 'Unrecognized git path')
  const session = findSession(store, sessionId)
  const scope = session ? getSessionGitScope(session) : []
  if (!assertRepoAllowed(scope, target.owner, target.repo)) {
    appendSessionEvent(store, sessionId, {
      type: 'status',
      payload: { type: 'status', status: `⚠ git proxy blocked out-of-scope repo ${target.owner}/${target.repo}` },
    })
    return bridgeError(403, 'forbidden', 'Repository not in session scope')
  }
  const pat = await getGithubToken()
  if (!pat) return bridgeError(503, 'github_unavailable', 'GitHub not connected — reconnect in Settings')
  if (target.kind === 'service') {
    // Audit the data op (not the chatty info/refs probe) to the server log.
    console.info(`[git-proxy] ${sessionId} ${target.owner}/${target.repo} ${target.service}`)
  }
  return forwardToGithub(req, target, pat)
}

export async function GET(req: Request, ctx: Ctx): Promise<Response> {
  return handle(req, ctx)
}
export async function POST(req: Request, ctx: Ctx): Promise<Response> {
  return handle(req, ctx)
}
