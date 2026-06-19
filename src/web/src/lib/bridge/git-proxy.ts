import 'server-only'

// Scoped-credential git proxy (policy + forwarding). The route in
// app/api/bridge/v1/code/sessions/[sessionId]/git/[...path] uses these to
// transparently reverse-proxy GitHub smart-HTTP: it reads owner/repo +
// operation from the URL (no packfile parsing), enforces the session's repo
// scope, and injects the REAL PAT host-side so it never enters the container.
// Validated against Anthropic's published Claude Code sandbox git-proxy design
// (credential/repo checks; token never in the sandbox).

export const GITHUB_GIT_BASE = 'https://github.com'

export type GitService = 'git-upload-pack' | 'git-receive-pack'

export interface GitRequest {
  owner: string
  repo: string // no trailing .git
  service: GitService
  kind: 'info-refs' | 'service'
}

const NAME = /^[A-Za-z0-9_.-]+$/
/** Reject path-traversal: a valid name char-set that is nothing but dots (`.`, `..`). */
function validName(s: string): boolean {
  return NAME.test(s) && !/^\.+$/.test(s)
}

/**
 * Parse the App-Router catch-all segments (everything after `.../git/`) into a
 * recognized smart-HTTP request, or null. Two shapes only (identical in
 * protocol v1 and v2 — v2 only changes the POST body negotiation):
 *   [owner, repo(.git), 'info', 'refs'] + ?service=git-(upload|receive)-pack
 *   [owner, repo(.git), 'git-(upload|receive)-pack']
 * Anything else (HEAD, objects/*, `..` traversal) → null (rejected, never
 * forwarded).
 */
export function parseGitRequest(segments: string[], search: URLSearchParams): GitRequest | null {
  if (segments.length < 3) return null
  const owner = segments[0]
  const repo = (segments[1] ?? '').replace(/\.git$/, '')
  if (!validName(owner) || !validName(repo)) return null
  const tail = segments.slice(2)
  if (tail.length === 2 && tail[0] === 'info' && tail[1] === 'refs') {
    const svc = search.get('service')
    if (svc === 'git-upload-pack' || svc === 'git-receive-pack') {
      return { owner, repo, service: svc, kind: 'info-refs' }
    }
    return null
  }
  if (tail.length === 1 && (tail[0] === 'git-upload-pack' || tail[0] === 'git-receive-pack')) {
    return { owner, repo, service: tail[0], kind: 'service' }
  }
  return null
}

/** Case- and `.git`-insensitive membership of `owner/repo` in the allowed set. */
export function assertRepoAllowed(allowedRepos: string[], owner: string, repo: string): boolean {
  const want = `${owner}/${repo}`.toLowerCase()
  return allowedRepos.some((r) => r.replace(/\.git$/, '').toLowerCase() === want)
}

/**
 * Transparently reverse-proxy ONE git smart-HTTP request to github.com with the
 * real PAT injected as Basic auth. Forwards only the headers git needs
 * (content-type, accept, git-protocol, user-agent) — deliberately NOT
 * accept-encoding, so undici decodes upstream and we pass plain bytes (avoids
 * the gzip double-decode proxy trap). Streams request + response bodies.
 */
export async function forwardToGithub(req: Request, target: GitRequest, pat: string): Promise<Response> {
  const path = target.kind === 'info-refs' ? 'info/refs' : target.service
  const url = new URL(`${GITHUB_GIT_BASE}/${target.owner}/${target.repo}.git/${path}`)
  if (target.kind === 'info-refs') url.searchParams.set('service', target.service)

  const headers = new Headers()
  headers.set('authorization', 'Basic ' + Buffer.from(`x-access-token:${pat}`).toString('base64'))
  for (const h of ['content-type', 'accept', 'git-protocol', 'user-agent']) {
    const v = req.headers.get(h)
    if (v) headers.set(h, v)
  }

  const method = req.method.toUpperCase()
  const hasBody = method !== 'GET' && method !== 'HEAD'
  const init: RequestInit & { duplex?: 'half' } = { method, headers, redirect: 'manual' }
  if (hasBody) {
    init.body = req.body
    init.duplex = 'half' // undici requires this when streaming a request body
  }
  const upstream = await fetch(url, init as RequestInit)

  const out = new Headers()
  for (const h of ['content-type', 'cache-control']) {
    const v = upstream.headers.get(h)
    if (v) out.set(h, v)
  }
  return new Response(upstream.body, { status: upstream.status, headers: out })
}
