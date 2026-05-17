/**
 * src/middleware.ts — bearer-token auth gate for the web app's API surface.
 *
 * Mirrors the bridge's auth pattern (src/cli/src/bridge/server.ts):
 *   - Requires `Authorization: Bearer <JARVIS_LOCAL_API_TOKEN>` on every
 *     /api/* request when JARVIS_REQUIRE_LOCAL_AUTH=1.
 *   - Token source: process.env.JARVIS_LOCAL_API_TOKEN. Same value the
 *     bridge uses — installed via start-desktop.sh into
 *     ~/.jarvis/local-api-token.env (chmod 600).
 *   - Public path allowlist: routes that legitimately need to be
 *     unauthenticated (e.g. healthchecks if any).
 *
 * Background: pre-2026-05-17 the web app had 60+ API routes including
 *   - /api/workspace/[id]/exec  (arbitrary shell)
 *   - /api/conversations  (DELETE wipes chat history)
 *   - /api/sessions  (read user data)
 *   - /api/workspace/[id]/file?path=.env  (key exfil)
 * …all of which trusted a hardcoded `LOCAL_USER_ID` constant and had
 * ZERO authentication. Anyone on the WiFi (or any browsed page via DNS
 * rebinding) could hit them via fetch(). The 2026-05-17 enterprise plan
 * §P0-SEC-6 flagged this as a P0.
 *
 * Failure mode for dev (no token configured): when
 * JARVIS_REQUIRE_LOCAL_AUTH is unset OR set to anything except "1",
 * the middleware allows through — preserves the dev UX. Once
 * JARVIS_REQUIRE_LOCAL_AUTH=1 is exported (start-desktop.sh does this),
 * the gate activates. Same opt-in pattern as the bridge.
 *
 * Token validation: constant-time compare (timingSafeEqual via Buffer).
 *
 * Refs: 2026-05-17 plan §P0-SEC-6.
 */
import { NextResponse } from 'next/server'
import type { NextRequest } from 'next/server'
import { timingSafeEqual } from 'node:crypto'

const REQUIRE_AUTH = process.env.JARVIS_REQUIRE_LOCAL_AUTH === '1'
const LOCAL_TOKEN = process.env.JARVIS_LOCAL_API_TOKEN ?? ''

// Public path allowlist. Anything matching is bypassed by the auth gate.
// Keep this MINIMAL — every entry is a route the bridge / Chrome ext /
// healthcheckers can hit without a token.
const PUBLIC_PATHS = new Set<string>([
  '/api/health',  // not currently a route but reserved for future
])

// Host header allowlist (DNS-rebinding defense, parallel to the bridge
// fix in commit f0150fb4). Even with a valid bearer token, requests
// whose Host header isn't 127.0.0.1 / localhost / [::1] are refused.
// CVE-2026-25253 "ClawJacked" pattern.
const HOST_ALLOWLIST = new Set<string>([
  '127.0.0.1',
  'localhost',
  '[::1]',
])

function hostFromHeader(host: string | null): string {
  if (!host) return ''
  // Strip port: "localhost:3000" → "localhost"; "[::1]:3000" → "[::1]"
  const lastColon = host.lastIndexOf(':')
  if (lastColon > 0 && !host.startsWith('[')) return host.slice(0, lastColon)
  if (host.startsWith('[') && host.includes(']')) return host.slice(0, host.indexOf(']') + 1)
  return host
}

function constantTimeStringEq(a: string, b: string): boolean {
  // timingSafeEqual requires equal-length buffers; use SHA-256 hashed
  // comparison for arbitrary-length strings, but for our case both
  // are the bearer-token shape — pad to common length.
  if (a.length !== b.length) return false
  try {
    return timingSafeEqual(Buffer.from(a, 'utf8'), Buffer.from(b, 'utf8'))
  } catch {
    return false
  }
}

export function middleware(req: NextRequest) {
  const url = new URL(req.url)
  const path = url.pathname

  // Only gate /api/* — pages render server-side and the bridge fix
  // handles the cross-origin attack vectors at the network boundary.
  if (!path.startsWith('/api/')) {
    return NextResponse.next()
  }

  // DNS-rebinding defense: Host header MUST be in the allowlist
  // regardless of token. Applies whether or not REQUIRE_AUTH is on.
  const hostBare = hostFromHeader(req.headers.get('host'))
  if (hostBare && !HOST_ALLOWLIST.has(hostBare)) {
    return new NextResponse(
      JSON.stringify({ error: 'host not allowed' }),
      { status: 403, headers: { 'Content-Type': 'application/json' } },
    )
  }

  // Auth gate.
  if (!REQUIRE_AUTH) {
    // Dev mode (no auth required). Pass through so `next dev` still
    // works without the user having to provision a token first.
    return NextResponse.next()
  }

  if (PUBLIC_PATHS.has(path)) {
    return NextResponse.next()
  }

  // Bearer token check.
  const authHeader = req.headers.get('authorization') ?? ''
  const match = /^Bearer\s+(.+)$/.exec(authHeader)
  if (!match || !LOCAL_TOKEN || !constantTimeStringEq(match[1], LOCAL_TOKEN)) {
    return new NextResponse(
      JSON.stringify({ error: 'auth required' }),
      {
        status: 401,
        headers: {
          'Content-Type': 'application/json',
          // Hint the client what kind of auth we want.
          'WWW-Authenticate': 'Bearer realm="jarvis-local"',
        },
      },
    )
  }

  return NextResponse.next()
}

// Match every API route. Page routes are NOT included — the web app's
// pages render server-side and trust env-level config; cross-origin
// reads of HTML pages aren't a meaningful threat with the current
// loopback-only binding.
export const config = {
  matcher: ['/api/:path*'],
}
