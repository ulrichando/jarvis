/**
 * src/proxy.ts — bearer-token auth gate (Next 16 proxy convention,
 * formerly middleware.ts — renamed per the middleware-to-proxy
 * deprecation) for the web app's API surface.
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
 * Token validation: constant-time compare via XOR-accumulate, kept
 * runtime-agnostic on purpose. Next 16 Proxy defaults to the Node.js
 * runtime (where node:crypto.timingSafeEqual exists), but the XOR has
 * the same constant-time property with zero imports, so it keeps working
 * unchanged if this is ever pinned to the Edge runtime.
 *
 * Refs: 2026-05-17 plan §P0-SEC-6.
 */
import { NextResponse } from 'next/server'
import type { NextRequest } from 'next/server'

const REQUIRE_AUTH = process.env.JARVIS_REQUIRE_LOCAL_AUTH === '1'
const LOCAL_TOKEN = process.env.JARVIS_LOCAL_API_TOKEN ?? ''

// JARVIS user-login gate (better-auth). Unauthenticated PAGE navigations
// redirect to /login. Independent of REQUIRE_AUTH (that's the network bearer
// gate). Escape hatch for dev: JARVIS_AUTH_DISABLED=1.
const AUTH_DISABLED = process.env.JARVIS_AUTH_DISABLED === '1'
// /share/<token> is a read-only public share page (token-gated, renders only
// the deployed site — never source/secrets), so it must be reachable without
// a login session.
const LOGIN_PUBLIC_PREFIXES = ['/login', '/signup', '/share']

function hasSessionCookie(req: NextRequest): boolean {
  // http (dev) vs __Secure- prefix (https/prod).
  return (
    req.cookies.has('better-auth.session_token') ||
    req.cookies.has('__Secure-better-auth.session_token')
  )
}

// Public path allowlist. Anything matching is bypassed by the auth gate.
// Keep this MINIMAL — every entry is a route the bridge / Chrome ext /
// healthcheckers can hit without a token.
const PUBLIC_PATHS = new Set<string>([
  '/api/health',  // desktop tray probe (probe_jarvis_web) — identity only
  // MCP OAuth callback: the provider redirects the browser here cross-site
  // (Sec-Fetch-Site: cross-site), so the same-origin carve-out can't apply and
  // there's no bearer to forward. It's safe to leave open — it does nothing
  // without a matching unguessable `state` (the OAuth CSRF guard) stored
  // server-side at /api/mcp/oauth/start, and it only redeems a one-time code.
  '/api/mcp/oauth/callback',
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
  // XOR-accumulate constant-time compare, kept dependency-free so it
  // works in any runtime. Next 16 Proxy runs in Node by default (so
  // node:crypto.timingSafeEqual is available), but this avoids the
  // import entirely: fixed iteration count over the full length, no
  // data-dependent branch. The length-mismatch early return matches
  // timingSafeEqual's own behavior (it also requires equal lengths);
  // token length is not a secret.
  if (a.length !== b.length) return false
  let diff = 0
  for (let i = 0; i < a.length; i++) {
    diff |= a.charCodeAt(i) ^ b.charCodeAt(i)
  }
  return diff === 0
}

export function proxy(req: NextRequest) {
  const url = new URL(req.url)
  const path = url.pathname

  // Page requests (not /api/*): JARVIS login gate. Unauthenticated page
  // navigations redirect to /login. Static assets are excluded by the
  // matcher; /login + /signup are public; /api/* falls through to the
  // network bearer gate below (and /api/auth/* is reached same-origin by
  // the login forms).
  if (!path.startsWith('/api/')) {
    if (
      !AUTH_DISABLED &&
      !LOGIN_PUBLIC_PREFIXES.some((p) => path === p || path.startsWith(`${p}/`)) &&
      !hasSessionCookie(req)
    ) {
      const loginUrl = new URL('/login', req.url)
      if (path !== '/') loginUrl.searchParams.set('next', path)
      return NextResponse.redirect(loginUrl)
    }
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

  // Same-origin browser carve-out: the web UI itself (pages served by
  // THIS server doing fetch()/EventSource against /api/*) has no way to
  // hold the bearer token — there is no client-side token wiring, and
  // EventSource can't set headers at all. Browsers stamp
  // `Sec-Fetch-Site: same-origin` on such requests and page JS cannot
  // forge it (forbidden header); a DNS-rebinding or cross-origin page
  // gets `cross-site` (and is killed by the Host allowlist above
  // anyway).
  //
  // But `Sec-Fetch-Site` is only unforgeable from *browsers*. A
  // non-browser caller (curl/script) can set it freely — and if the box
  // is ever fronted by a default reverse proxy (which rewrites Host to
  // the localhost upstream, defeating the allowlist above), a *remote*
  // forged-header request would otherwise sail past the bearer gate. So
  // tie the carve-out to an authenticated session: the logged-in UI's
  // fetch()/EventSource always send the session cookie, so requiring it
  // costs legit traffic nothing, but a forged `Sec-Fetch-Site` with no
  // session cookie now falls through to the bearer check below.
  // `/api/auth/*` is exempt — it's the same-origin POST that CREATES the
  // session (no cookie exists yet), and the public `/share/*` surface is
  // a page route (served via /share/[token]/asset/…, not /api/*), so it
  // never reaches this gate.
  if (
    req.headers.get('sec-fetch-site') === 'same-origin' &&
    (path.startsWith('/api/auth/') || hasSessionCookie(req))
  ) {
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

// Match /api/* (bearer gate) AND page routes (login gate), excluding Next
// internals + static assets (so images/fonts/css aren't redirected to /login).
export const config = {
  matcher: [
    '/((?!_next/static|_next/image|favicon.ico|.*\\.(?:png|jpg|jpeg|gif|svg|ico|webp|avif|woff|woff2|ttf|otf|css|js|map|txt|xml|json)).*)',
  ],
}
