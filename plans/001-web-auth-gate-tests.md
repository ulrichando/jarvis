# Plan 001: The web API auth gate (`proxy()`) has direct unit-test coverage

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in "STOP conditions" occurs, stop and report — do not
> improvise. When done, update this plan's row in `plans/README.md`.
>
> **Drift check (run first)**:
> `git diff --stat f6efd301..HEAD -- src/web/src/proxy.ts`
> If `src/web/src/proxy.ts` changed since this plan was written, compare the
> "Current state" excerpt below against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: LOW (adds tests only; no production code changes)
- **Depends on**: none
- **Category**: tests / security
- **Planned at**: commit `f6efd301`, 2026-06-22

## Why this matters

`src/web/src/proxy.ts` is the single authentication boundary for the entire web
app — it gates every `/api/*` route, including arbitrary shell exec
(`/api/workspace/[id]/exec`), chat deletion, and `.env` reads. It implements a
bearer-token constant-time compare, a DNS-rebinding Host allowlist, and a
same-origin `Sec-Fetch-Site` carve-out tied to a session cookie. **None of this
is tested**: `src/web/tests/bridge/auth.test.ts` only covers the trivial
`extractBearer()` string helper. A one-character regression in the carve-out or
the Host check silently re-exposes the whole API to the LAN, and CI would stay
green. This plan adds focused unit tests so any such regression fails loudly.

## Current state

- `src/web/src/proxy.ts` — exports `proxy(req: NextRequest)` and `config`. It
  reads its switches from env **at module load** (load-bearing for the test
  setup):
  - `const REQUIRE_AUTH = process.env.JARVIS_REQUIRE_LOCAL_AUTH === '1'` (line 42)
  - `const LOCAL_TOKEN = process.env.JARVIS_LOCAL_API_TOKEN ?? ''` (line 43)
  - `const AUTH_DISABLED = process.env.JARVIS_AUTH_DISABLED === '1'` (line 48)
  - `const CANONICAL_HOST = process.env.JARVIS_CANONICAL_HOST ?? '127.0.0.1'` (read inside `proxy()`, line 128)

  The gate logic, in order (see lines 110–226):
  1. Page routes (`!path.startsWith('/api/')`): redirect `localhost`/`[::1]` →
     `CANONICAL_HOST` (307); then login-gate (redirect to `/login` if no session
     cookie and path isn't in `LOGIN_PUBLIC_PREFIXES`); else `NextResponse.next()`.
  2. `/api/*`: **Host allowlist** — if Host header bare-host ∉
     `{127.0.0.1, localhost, [::1]}` → `403 {"error":"host not allowed"}`.
  3. If `!REQUIRE_AUTH` → `NextResponse.next()` (dev passthrough).
  4. If path ∈ `PUBLIC_PATHS` (`/api/health`, `/api/mcp/oauth/callback`) → next.
  5. **Same-origin carve-out**: if `sec-fetch-site === 'same-origin'` AND
     (`path` starts `/api/auth/` OR a session cookie is present) → next.
  6. **Bearer check**: `Authorization: Bearer <token>` constant-time-compared to
     `LOCAL_TOKEN`; mismatch/absent → `401 {"error":"auth required"}` with a
     `WWW-Authenticate` header.

  Session cookie presence = `better-auth.session_token` or
  `__Secure-better-auth.session_token` cookie (see `hasSessionCookie`, line 54).

- **Test conventions** — vitest, files under `src/web/tests/bridge/`. Exemplar
  for structure/imports: `src/web/tests/bridge/auth.test.ts`:
  ```ts
  import { describe, expect, test } from 'vitest'
  import { extractBearer } from '@/lib/bridge/auth'
  describe('extractBearer', () => {
    test('...', () => { expect(extractBearer('Bearer abc123')).toBe('abc123') })
  })
  ```
  `@/` resolves to `src/web/src/`, so import the gate as `import { proxy } from '@/proxy'`.

- **Critical testing detail**: because `REQUIRE_AUTH`/`LOCAL_TOKEN`/`AUTH_DISABLED`
  are evaluated once at import, you cannot change them after importing. Use
  `vi.resetModules()` + `vi.stubEnv()` + a dynamic `await import('@/proxy')`
  **per env configuration** (helper provided in Step 2).

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Run the new test file | `cd src/web && npx vitest run tests/bridge/proxy.test.ts` | all tests pass |
| Run the full web suite | `cd src/web && npx vitest run` | no new failures vs. baseline |

(Run vitest from `src/web`, never the repo root — the `@/` alias breaks at root.)

## Scope

**In scope** (only file you create):
- `src/web/tests/bridge/proxy.test.ts` (create)

**Out of scope** (do NOT touch):
- `src/web/src/proxy.ts` — this plan only TESTS it; do not "fix" it. If a test
  reveals a real gate bug, that is a STOP condition (report it; don't patch).
- `src/web/tests/bridge/auth.test.ts` — leave as-is.

## Git workflow

- Branch: `advisor/001-web-auth-gate-tests`
- One commit; message style is conventional commits (see `git log --oneline -5`),
  e.g. `test(web): cover proxy() auth gate (host allowlist, bearer, carve-out)`.
- Do NOT push or open a PR unless the operator instructed it.

## Steps

### Step 1: Build a request helper and confirm the file runs

Create `src/web/tests/bridge/proxy.test.ts`. Add a helper that builds a
`NextRequest` with arbitrary headers (the gate reads `host`, `authorization`,
`sec-fetch-site`, and the `cookie` header):

```ts
import { afterEach, describe, expect, test, vi } from 'vitest'
import { NextRequest } from 'next/server'

function req(
  path: string,
  headers: Record<string, string> = {},
): NextRequest {
  return new NextRequest(`http://127.0.0.1:3000${path}`, {
    headers: { host: '127.0.0.1:3000', ...headers },
  })
}

// Load proxy() fresh with a specific env (module reads env at import time).
async function loadProxy(env: Record<string, string>) {
  vi.resetModules()
  vi.unstubAllEnvs()
  for (const [k, v] of Object.entries(env)) vi.stubEnv(k, v)
  return (await import('@/proxy')).proxy
}

afterEach(() => vi.unstubAllEnvs())
```

**Verify**: `cd src/web && npx vitest run tests/bridge/proxy.test.ts`
→ runs with 0 tests (or add a trivial `test('loads', () => expect(true).toBe(true))`); exit 0.

### Step 2: Cover the auth-OFF and Host-allowlist paths

Add these cases (a `NextResponse.next()` result has `response.status === 200`
and no `location` header; a redirect has status 307/308 + `location`):

- **auth disabled passes through**: `loadProxy({ JARVIS_REQUIRE_LOCAL_AUTH: '0' })`,
  call `proxy(req('/api/anything'))`, expect `.status === 200` (next, no body error).
- **Host not allowed → 403**: with `JARVIS_REQUIRE_LOCAL_AUTH: '1'`, call
  `proxy(req('/api/x', { host: 'evil.example.com' }))`; expect `.status === 403`.
- **loopback Host variants pass the allowlist**: `host: 'localhost:3000'` and
  `host: '[::1]:3000'` do NOT 403 (they proceed to the bearer check).

**Verify**: `cd src/web && npx vitest run tests/bridge/proxy.test.ts` → all pass.

### Step 3: Cover the bearer + carve-out paths (the security core)

With `loadProxy({ JARVIS_REQUIRE_LOCAL_AUTH: '1', JARVIS_LOCAL_API_TOKEN: 'secret-token' })`:

- **no bearer → 401**: `proxy(req('/api/x'))` → `.status === 401`.
- **wrong bearer → 401**: `authorization: 'Bearer wrong'` → `.status === 401`.
- **correct bearer → next**: `authorization: 'Bearer secret-token'` → `.status === 200`.
- **public path bypass**: `proxy(req('/api/health'))` → `.status === 200` even with no bearer.
- **same-origin + session cookie → next**:
  `{ 'sec-fetch-site': 'same-origin', cookie: 'better-auth.session_token=abc' }`,
  no bearer → `.status === 200`.
- **forged same-origin WITHOUT cookie → 401** (this is the carve-out's whole
  point): `{ 'sec-fetch-site': 'same-origin' }`, no cookie, no bearer →
  `.status === 401`.
- **`/api/auth/*` same-origin with no cookie → next** (login POST creates the
  session): `proxy(req('/api/auth/login', { 'sec-fetch-site': 'same-origin' }))`
  → `.status === 200`.

**Verify**: `cd src/web && npx vitest run tests/bridge/proxy.test.ts` → all pass.

### Step 4: Cover one page-route login redirect

With `loadProxy({ JARVIS_REQUIRE_LOCAL_AUTH: '1', JARVIS_LOCAL_API_TOKEN: 't' })`:
- **unauthenticated page → /login**: `proxy(req('/code'))` (no session cookie)
  → status 307/308 and the `location` header contains `/login`.
- **page with session cookie → next**: `cookie: 'better-auth.session_token=abc'`
  on `/code` → `.status === 200`.

**Verify**: `cd src/web && npx vitest run tests/bridge/proxy.test.ts` → all pass.

## Test plan

- New file `src/web/tests/bridge/proxy.test.ts`, ~12 cases across Steps 2–4
  covering: auth-off passthrough, Host allowlist (deny + loopback variants),
  bearer (absent/wrong/correct), public-path bypass, same-origin carve-out
  (with cookie, without cookie, `/api/auth/*`), and page login redirect.
- Structural pattern: `src/web/tests/bridge/auth.test.ts` (imports, `describe`/`test`).
- Verification: `cd src/web && npx vitest run tests/bridge/proxy.test.ts`
  → all pass; then `cd src/web && npx vitest run` → no new failures.

## Done criteria

ALL must hold:

- [ ] `cd src/web && npx vitest run tests/bridge/proxy.test.ts` exits 0 with ≥10 passing tests.
- [ ] `cd src/web && npx vitest run` shows no new failures vs. the pre-change baseline.
- [ ] The "forged same-origin without cookie → 401" case exists and passes.
- [ ] `git status` shows only `src/web/tests/bridge/proxy.test.ts` added; `src/web/src/proxy.ts` unchanged.
- [ ] `plans/README.md` row for 001 updated.

## STOP conditions

Stop and report (do not improvise) if:

- The excerpt in "Current state" doesn't match `src/web/src/proxy.ts` (drift).
- A test you wrote to assert a SECURE outcome (e.g. forged-header → 401) instead
  shows the gate lets the request through — that is a real vulnerability, not a
  test bug. Report it; do NOT modify `proxy.ts` to make the test pass.
- `NextRequest` can't be constructed in the vitest environment after a
  reasonable attempt (e.g. needs `environment: 'node'`); report the blocker.

## Maintenance notes

- If a new always-public route is added to `PUBLIC_PATHS`, add a test asserting
  it bypasses the bearer check (and confirm it truly needs to).
- The module reads env at import; any future test that changes auth config must
  use the `loadProxy()` reset-and-reimport pattern, not set env mid-test.
- Reviewer: scrutinize that the "without cookie" carve-out case asserts **401**,
  not 200 — inverting it would document the vulnerability as correct.
