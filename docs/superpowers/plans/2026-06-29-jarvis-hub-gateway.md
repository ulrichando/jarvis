# JARVIS Hub Gateway (sub-project 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the existing `:4000` CLI proxy as a VPS-hosted **Hub Gateway** — add an OpenAI-shaped ingress so every client shape can route through it, containerize it into the `src/web` stack behind Caddy at `/hub`, and expose a `/config` diagnostic. Keys already live on the VPS.

**Architecture:** The proxy (`src/cli/src/proxy/server.ts`) today ingests Anthropic `/v1/messages` only and translates to OpenAI `/chat/completions` upstream. We add a second ingress, `POST /v1/chat/completions`, that is a **pure passthrough** (OpenAI-shape in → provider → OpenAI-shape out, no conversion — `executeWithFallback` already POSTs OpenAI-shape upstream). The proxy runs as a `hub` container that binds `0.0.0.0` *inside* the compose network (never a published port); Caddy reverse-proxies `/hub/*` to it with the `/hub` prefix **stripped**, so client base-URLs carry `/hub` while the proxy serves its native `/v1/*`. Auth is the existing login-JWT gate (`verifyProxyToken` already enforces `exp`/`aud`/`iss`/HS256) — sub-project 1 just turns it on via `JARVIS_PROXY_AUTH_REQUIRED=1`.

**Tech Stack:** Bun + TypeScript (proxy), Docker Compose, Caddy v2, Cloudflare (edge). Tests via `bun test`.

---

## File Structure

- **Create** `src/cli/src/proxy/hubGateway.ts` — pure, testable helpers: `classifyChatCompletionsRequest(model)` (route vs reject-Anthropic) and `buildHubConfig()` (diagnostic).
- **Create** `src/cli/src/proxy/hubGateway.test.ts` — bun unit tests for both helpers.
- **Modify** `src/cli/src/proxy/server.ts` — add the `/v1/chat/completions` handler + `/config` route, both wired to the helpers; reuse the existing auth gate, `executeWithFallback`, and logging.
- **Create** `src/cli/Dockerfile.hub` — Bun image that runs `src/proxy/server.ts`.
- **Modify** `src/web/docker-compose.yml` — add the `hub` service; `caddy` depends on it.
- **Modify** `src/web/Caddyfile` — add the `/hub/*` route (prefix-stripped).
- **Create** `docs/runbook/hub-gateway-deploy.md` — Cloudflare Access exclusion + deploy + both-shape smoke.

Verification note (CLI tree): the proxy is hand-written source (not a compiled React artifact), so `bun test` + `bun build <file> --no-bundle` are valid here. Run all `bun` commands from `src/cli`.

---

## Task 1: OpenAI-shaped ingress (`/v1/chat/completions`)

**Files:**
- Create: `src/cli/src/proxy/hubGateway.ts`
- Create: `src/cli/src/proxy/hubGateway.test.ts`
- Modify: `src/cli/src/proxy/server.ts` (add handler + route)

- [ ] **Step 1: Write the failing test** — `src/cli/src/proxy/hubGateway.test.ts`

```ts
import { beforeAll, describe, expect, test } from 'bun:test'

beforeAll(() => {
  process.env.DEEPSEEK_API_KEY = 'test-deepseek'
  process.env.ANTHROPIC_API_KEY = 'test-anthropic'
  process.env.OPENAI_API_KEY = 'test-openai'
  process.env.KIMI_API_KEY = 'test-kimi'
  process.env.GOOGLE_API_KEY = 'test-gemini'
  process.env.GEMINI_API_KEY = 'test-gemini'
  // Deterministic default provider for the no-model case.
  process.env.JARVIS_PROVIDER = 'deepseek'
})

import { classifyChatCompletionsRequest } from './hubGateway.js'

describe('classifyChatCompletionsRequest', () => {
  test('OpenAI-family model routes to its provider', () => {
    const r = classifyChatCompletionsRequest('deepseek-v4-flash')
    expect(r.kind).toBe('route')
    if (r.kind === 'route') expect(r.provider.name).toBe('deepseek')
  })

  test('Anthropic model is rejected — must use /v1/messages', () => {
    const r = classifyChatCompletionsRequest('claude-haiku-4-5')
    expect(r.kind).toBe('reject')
    if (r.kind === 'reject') expect(r.status).toBe(400)
  })

  test('absent model falls back to the default provider (non-anthropic here)', () => {
    const r = classifyChatCompletionsRequest(undefined)
    expect(r.kind).toBe('route')
    if (r.kind === 'route') expect(r.provider.name).toBe('deepseek')
  })
})
```

- [ ] **Step 2: Run it, verify it fails**

Run: `cd src/cli && bun test src/proxy/hubGateway.test.ts`
Expected: FAIL — `Cannot find module './hubGateway.js'`.

- [ ] **Step 3: Implement the helper** — `src/cli/src/proxy/hubGateway.ts`

```ts
import { getProvider, getProviderForModel, type Provider } from './providers.js'

export type ChatCompletionsRoute =
  | { kind: 'route'; provider: Provider }
  | { kind: 'reject'; status: number; message: string }

/**
 * Decide where an OpenAI-shaped /v1/chat/completions request goes.
 *
 * Anthropic models are rejected here: the proxy's `anthropic` provider speaks
 * native /v1/messages, not /chat/completions, so an OpenAI-shaped request for a
 * Claude model can't be served on this path. No real client does this — voice
 * uses the Anthropic plugin (→ /v1/messages) for Claude — so it's a defensive
 * 400, not a supported route. (OpenAI-ingress→Anthropic-upstream is explicitly
 * out of scope for sub-project 1.)
 */
export function classifyChatCompletionsRequest(
  model: string | undefined,
): ChatCompletionsRoute {
  const provider = (model ? getProviderForModel(model) : null) ?? getProvider()
  if (provider.name === 'anthropic') {
    return {
      kind: 'reject',
      status: 400,
      message:
        'Anthropic models must use the /v1/messages endpoint, not /v1/chat/completions',
    }
  }
  return { kind: 'route', provider }
}
```

- [ ] **Step 4: Run the test, verify it passes**

Run: `cd src/cli && bun test src/proxy/hubGateway.test.ts`
Expected: PASS (3 tests).

- [ ] **Step 5: Wire the ingress handler into `server.ts`**

In the top-level `Bun.serve({ fetch })` (after the `/v1/messages` branch at ~line 227-229), add:

```ts
    if (req.method === 'POST' &&
        (url.pathname.endsWith('/chat/completions') || url.pathname === '/v1/chat/completions')) {
      return handleChatCompletionsRequest(req, url)
    }
```

Add the import near the top of `server.ts`:

```ts
import { classifyChatCompletionsRequest, buildHubConfig } from './hubGateway.js'
```

Add the handler (place after `handleMessagesRequest`):

```ts
// OpenAI-shaped ingress. The proxy's upstream call is already
// `<baseUrl>/chat/completions`, so OpenAI-shape in → OpenAI-shape out is a pure
// PASSTHROUGH — no Anthropic⇆OpenAI conversion (that's only the /v1/messages
// path). Reuses the same auth gate, fallback chain, and request logger.
async function handleChatCompletionsRequest(req: Request, url: URL): Promise<Response> {
  const requestId = newRequestId()
  const tsStart = Date.now()
  const baseLog: RequestLog = {
    ts: new Date().toISOString(), request_id: requestId, path: url.pathname,
    provider: null, upstream_model: null, client_model: null, status: 200,
    error_type: null, error_message: null, latency_ms: 0, ttfb_ms: null,
    input_tokens: null, output_tokens: null, cache_read_tokens: null,
    retries_used: 0, fallback_used: false, primary_provider_error: null,
    stream: false, stop_reason: null,
  }
  const finish = (entry: Partial<RequestLog>) =>
    logRequest({ ...baseLog, ...entry, latency_ms: Date.now() - tsStart })

  const authErr = checkInboundAuth(req.headers)
  if (authErr) {
    finish({ status: authErr.status, error_type: 'unauthorized', error_message: authErr.message })
    return new Response(
      JSON.stringify({ type: 'error', error: { type: 'authentication_error', message: authErr.message } }),
      { status: authErr.status, headers: { 'Content-Type': 'application/json' } })
  }

  let openaiReq: any
  try { openaiReq = await req.json() } catch {
    finish({ status: 400, error_type: 'invalid_request_error', error_message: 'invalid JSON' })
    return new Response(JSON.stringify({ error: { message: 'Invalid JSON', type: 'invalid_request_error' } }),
      { status: 400, headers: { 'Content-Type': 'application/json' } })
  }

  baseLog.client_model = openaiReq.model ?? null
  const isStream = openaiReq.stream === true
  baseLog.stream = isStream

  const route = classifyChatCompletionsRequest(openaiReq.model)
  if (route.kind === 'reject') {
    finish({ status: route.status, error_type: 'invalid_request_error', error_message: route.message })
    return new Response(JSON.stringify({ error: { message: route.message, type: 'invalid_request_error' } }),
      { status: route.status, headers: { 'Content-Type': 'application/json' } })
  }
  baseLog.provider = route.provider.name
  baseLog.upstream_model = route.provider.model

  const outcome = await executeWithFallback(route.provider, openaiReq)
  if (!outcome.response) {
    const errMsg = outcome.errorMessage ?? 'upstream unreachable'
    finish({ status: 502, error_type: 'upstream_unreachable', error_message: errMsg,
      retries_used: outcome.retriesUsed, fallback_used: outcome.fallbackUsed,
      primary_provider_error: outcome.primaryError, provider: outcome.provider.name,
      upstream_model: outcome.provider.model })
    return new Response(JSON.stringify({ error: { message: errMsg, type: 'api_error' } }),
      { status: 502, headers: { 'Content-Type': 'application/json' } })
  }

  baseLog.retries_used = outcome.retriesUsed
  baseLog.fallback_used = outcome.fallbackUsed
  baseLog.primary_provider_error = outcome.primaryError
  baseLog.ttfb_ms = outcome.ttfbMs
  const stdHeaders = {
    'x-jarvis-request-id': requestId,
    'x-jarvis-provider': outcome.provider.name,
    'x-jarvis-fallback-used': String(outcome.fallbackUsed),
  }

  if (isStream) {
    // ponytail: per-token logging omitted on the stream passthrough — would
    // require teeing the stream to parse the trailing `usage` chunk. Provider /
    // status / latency are still logged via the flush hook. Upgrade path: tee +
    // parse usage if stream token accounting is needed.
    const logged = outcome.response.body!.pipeThrough(new TransformStream<Uint8Array, Uint8Array>({
      flush() { finish({ provider: outcome.provider.name, upstream_model: outcome.provider.model }) },
    }))
    return new Response(logged, {
      headers: { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache',
        'Connection': 'keep-alive', ...stdHeaders },
    })
  }

  const rawText = await outcome.response.text()
  let usage: any
  try { usage = JSON.parse(rawText)?.usage } catch {}
  finish({ input_tokens: usage?.prompt_tokens ?? null, output_tokens: usage?.completion_tokens ?? null })
  return new Response(rawText, { headers: { 'Content-Type': 'application/json', ...stdHeaders } })
}
```

- [ ] **Step 6: Verify it parses + nothing else broke**

Run: `cd src/cli && bun build src/proxy/server.ts --no-bundle && bun test src/proxy/`
Expected: build prints the parsed output (no error); existing proxy tests + the new `hubGateway.test.ts` pass.

- [ ] **Step 7: Live loopback smoke (real provider, local keys still present)**

```bash
cd src/cli && JARVIS_PROXY_PORT=4099 bun run src/proxy/server.ts &  # uses ~/.jarvis/keys.env
sleep 2
curl -sS http://127.0.0.1:4099/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"deepseek-v4-flash","stream":false,"messages":[{"role":"user","content":"reply with OK"}]}' | head -c 400
kill %1
```
Expected: a JSON OpenAI-shaped completion containing assistant text. (Auth is off on loopback — `AUTH_REQUIRED` defaults off — so no token needed for this local check.)

- [ ] **Step 8: Commit**

```bash
git add src/cli/src/proxy/hubGateway.ts src/cli/src/proxy/hubGateway.test.ts src/cli/src/proxy/server.ts
git commit -m "feat(hub): OpenAI-shaped ingress on the proxy (/v1/chat/completions passthrough)" -- src/cli/src/proxy/hubGateway.ts src/cli/src/proxy/hubGateway.test.ts src/cli/src/proxy/server.ts
```

---

## Task 2: `/config` diagnostic endpoint

**Files:**
- Modify: `src/cli/src/proxy/hubGateway.ts` (add `buildHubConfig`)
- Modify: `src/cli/src/proxy/hubGateway.test.ts` (add tests)
- Modify: `src/cli/src/proxy/server.ts` (add `/config` route)

- [ ] **Step 1: Add the failing test** to `hubGateway.test.ts`

```ts
import { classifyChatCompletionsRequest, buildHubConfig } from './hubGateway.js'

describe('buildHubConfig', () => {
  test('reports provider key-presence from env', () => {
    const cfg = buildHubConfig()
    expect(cfg.status).toBe('ok')
    expect(cfg.providers.deepseek).toBe(true)   // set in beforeAll
    expect(cfg.default_provider).toBe('deepseek')
  })

  test('a provider with no key reads false', () => {
    const saved = process.env.OPENAI_API_KEY
    delete process.env.OPENAI_API_KEY
    expect(buildHubConfig().providers.openai).toBe(false)
    process.env.OPENAI_API_KEY = saved
  })
})
```

(Update the existing import line to add `buildHubConfig`.)

- [ ] **Step 2: Run it, verify it fails**

Run: `cd src/cli && bun test src/proxy/hubGateway.test.ts`
Expected: FAIL — `buildHubConfig is not a function`.

- [ ] **Step 3: Implement `buildHubConfig`** in `hubGateway.ts`

```ts
// Diagnostic: which providers have a key on this host + the default route. No
// secrets are returned — only booleans. The provider→envvar map is the small
// stable set in ~/.jarvis/keys.env; add a line when a provider is added.
const _DIAG_PROVIDER_KEYS: Record<string, string> = {
  deepseek: 'DEEPSEEK_API_KEY',
  anthropic: 'ANTHROPIC_API_KEY',
  openai: 'OPENAI_API_KEY',
  kimi: 'KIMI_API_KEY',
  google: 'GOOGLE_API_KEY',
}

export function buildHubConfig(): {
  status: string
  default_provider: string | null
  default_model: string | null
  providers: Record<string, boolean>
} {
  const providers: Record<string, boolean> = {}
  for (const [name, envVar] of Object.entries(_DIAG_PROVIDER_KEYS)) {
    providers[name] = Boolean((process.env[envVar] ?? '').trim())
  }
  let default_provider: string | null = null
  let default_model: string | null = null
  try { const p = getProvider(); default_provider = p.name; default_model = p.model } catch {}
  return { status: 'ok', default_provider, default_model, providers }
}
```

- [ ] **Step 4: Run the test, verify it passes**

Run: `cd src/cli && bun test src/proxy/hubGateway.test.ts`
Expected: PASS.

- [ ] **Step 5: Add the route** in `server.ts` `fetch()` (after the chat-completions branch). Match both forms so it works whether or not Caddy strips `/hub`, and gate it behind auth (it reveals provider availability):

```ts
    if (req.method === 'GET' && (url.pathname === '/config' || url.pathname === '/hub/config')) {
      const authErr = checkInboundAuth(req.headers)
      if (authErr) {
        return new Response(
          JSON.stringify({ type: 'error', error: { type: 'authentication_error', message: authErr.message } }),
          { status: authErr.status, headers: { 'Content-Type': 'application/json' } })
      }
      return new Response(JSON.stringify(buildHubConfig()), {
        headers: { 'Content-Type': 'application/json' },
      })
    }
```

- [ ] **Step 6: Verify + commit**

Run: `cd src/cli && bun build src/proxy/server.ts --no-bundle && bun test src/proxy/`
Expected: parses; all proxy tests pass.

```bash
git add src/cli/src/proxy/hubGateway.ts src/cli/src/proxy/hubGateway.test.ts src/cli/src/proxy/server.ts
git commit -m "feat(hub): /config diagnostic endpoint (provider key-presence + default route)" -- src/cli/src/proxy/hubGateway.ts src/cli/src/proxy/hubGateway.test.ts src/cli/src/proxy/server.ts
```

---

## Task 3: Hub container image

**Files:**
- Create: `src/cli/Dockerfile.hub`

- [ ] **Step 1: Write the Dockerfile** — `src/cli/Dockerfile.hub`

```dockerfile
# JARVIS Hub Gateway — the CLI's :4000 proxy, containerized for the VPS.
# Runs from source via Bun, exactly like bin/jarvis's proxy-runtime.sh. The
# proxy's import graph is a clean subset (no React, no native/feature() modules),
# so `bun run src/proxy/server.ts` never loads the heavy CLI graph.
FROM oven/bun:1
WORKDIR /app

# Deps first for layer caching. (If a native/optional dep blocks the frozen
# install in-container, the non-frozen retry resolves it — the proxy doesn't
# import those modules at runtime anyway.)
COPY package.json bun.lock* ./
RUN bun install --frozen-lockfile || bun install

COPY . .

# Public deployment posture: bind all interfaces (only Caddy reaches it on the
# compose network — never a published host port) and REQUIRE the login JWT.
ENV JARVIS_PROXY_HOST=0.0.0.0 \
    JARVIS_ALLOW_PUBLIC_BIND=1 \
    JARVIS_PROXY_AUTH_REQUIRED=1 \
    JARVIS_PROXY_PORT=4000

EXPOSE 4000
CMD ["bun", "run", "src/proxy/server.ts"]
```

- [ ] **Step 2: Build it standalone to catch install/runtime errors early**

Run: `docker build -f src/cli/Dockerfile.hub -t jarvis-hub:test src/cli`
Expected: image builds. (If `bun install` fails on a native dep, switch the install line to `bun install --omit=optional || bun install`; the runtime path doesn't use those modules.)

- [ ] **Step 3: Smoke the container in isolation** (auth required → expect 401 without a token, proving the gate is live; `/health` is exempt)

```bash
docker run --rm -d --name jarvis-hub-test -p 4100:4000 \
  -e JARVIS_PROVIDER=deepseek -e DEEPSEEK_API_KEY=dummy \
  -e JARVIS_PROXY_JWT_SECRET=testsecret jarvis-hub:test
sleep 3
curl -s http://127.0.0.1:4100/health                      # → {"status":"ok"}
curl -s -o /dev/null -w '%{http_code}\n' \
  http://127.0.0.1:4100/v1/chat/completions -X POST \
  -H 'Content-Type: application/json' -d '{"model":"deepseek-v4-flash"}'   # → 401
docker rm -f jarvis-hub-test
```
Expected: `/health` returns ok; the unauthenticated POST returns `401` (auth gate enforced).

- [ ] **Step 4: Commit**

```bash
git add src/cli/Dockerfile.hub
git commit -m "feat(hub): Dockerfile for the VPS hub gateway (Bun proxy, auth-required)" -- src/cli/Dockerfile.hub
```

---

## Task 4: Compose service + Caddy route

**Files:**
- Modify: `src/web/docker-compose.yml`
- Modify: `src/web/Caddyfile`

- [ ] **Step 1: Add the `hub` service** to `src/web/docker-compose.yml` (after the `web` service, mirroring its `env_file` + internal-only `expose` pattern):

```yaml
  # Hub Gateway (JARVIS Hub sub-project 1): the CLI's :4000 proxy, holding the
  # provider keys + verifying login JWTs. Binds 0.0.0.0 in-container so Caddy
  # reaches it over the docker network; NOT published to the host (only the
  # Caddy /hub route fronts it). Keys + JARVIS_PROXY_JWT_SECRET come from
  # .env.production (already present).
  hub:
    build:
      context: ../cli
      dockerfile: Dockerfile.hub
    restart: unless-stopped
    env_file:
      - path: .env.production
        required: false
    environment:
      JARVIS_PROXY_HOST: "0.0.0.0"
      JARVIS_ALLOW_PUBLIC_BIND: "1"
      JARVIS_PROXY_AUTH_REQUIRED: "1"
      JARVIS_PROXY_PORT: "4000"
      JARVIS_PROVIDER: "${JARVIS_PROVIDER:-deepseek}"
    expose: ["4000"]
    healthcheck:
      test: ["CMD", "bun", "-e", "fetch('http://127.0.0.1:4000/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"]
      interval: 10s
      timeout: 5s
      retries: 5
```

- [ ] **Step 2: Make Caddy depend on the hub** — change the `caddy` service's `depends_on: [web]` to:

```yaml
    depends_on: [web, hub]
```

- [ ] **Step 3: Add the `/hub` route** to `src/web/Caddyfile`, inside the site block, **before** the `reverse_proxy web:3000` catch-all (place it next to the `@pty` block):

```caddyfile
	# Hub Gateway (sub-project 1): JWT-gated multi-provider LLM proxy. Strip the
	# /hub prefix so client base-URLs carry it while the proxy serves native
	# /v1/* (+ /config). Cloudflare Access EXCLUDES /hub — clients authenticate
	# with the login JWT, not the Access cookie (see docs/runbook/hub-gateway-deploy.md).
	handle_path /hub/* {
		reverse_proxy hub:4000
	}
```

- [ ] **Step 4: Validate the compose + Caddy config**

Run:
```bash
cd src/web && docker compose config >/dev/null && echo "compose OK"
docker compose run --rm --no-deps --entrypoint caddy caddy validate --config /etc/caddy/Caddyfile
```
Expected: `compose OK`; Caddy reports `Valid configuration`. (If Caddy warns about mixing `handle_path` with the bare `reverse_proxy web:3000`, wrap the `@pty` route and the catch-all each in their own `handle`/`handle_path` block and re-validate.)

- [ ] **Step 5: Commit**

```bash
git add src/web/docker-compose.yml src/web/Caddyfile
git commit -m "feat(hub): wire hub container into the web compose stack + Caddy /hub route" -- src/web/docker-compose.yml src/web/Caddyfile
```

---

## Task 5: Deploy runbook + end-to-end smoke

**Files:**
- Create: `docs/runbook/hub-gateway-deploy.md`

- [ ] **Step 1: Write the runbook** — `docs/runbook/hub-gateway-deploy.md`

It must document, in order:

1. **Drop the stale Groq key** from `src/web/.env.production` (left over from the Groq eradication — `GROQ_API_KEY=…`). One line; nothing reads it.
2. **Confirm `.env.production` has** `JARVIS_PROXY_JWT_SECRET` + the provider keys (verified 2026-06-29) — no action, just the check command:
   `grep -oE '^(JARVIS_PROXY_JWT_SECRET|[A-Z]+_API_KEY)' src/web/.env.production | sort -u`
3. **Cloudflare Access exclusion for `/hub`** (dashboard, not code): Zero Trust → Access → Applications → the `0wlan.com` app → add a path **bypass** for `/hub/*` (or define a second self-hosted app scoped to `0wlan.com/hub` with a **Bypass / Service Auth** policy). Rationale: hub clients present the login JWT in the `Authorization` header; they do not carry the Access cookie, so without the bypass Access would 302 them to the login page and every client request would fail. This mirrors the existing `/install.sh` + `/releases` exclusions.
4. **Deploy:** `cd src/web && docker compose up -d --build hub && docker compose up -d caddy`.
5. **Health:** `docker compose ps hub` shows healthy; `docker compose logs hub | tail` shows `inbound auth: REQUIRED (login token)` and `Ready — provider: …`.

- [ ] **Step 2: End-to-end smoke through Caddy/Cloudflare — both shapes**

Mint a token with `jarvis auth login` (or read the current one from `~/.claude/.credentials.json`), then:

```bash
TOKEN=…   # a valid login JWT
# Diagnostic:
curl -s https://0wlan.com/hub/config -H "Authorization: Bearer $TOKEN" | head -c 300
# OpenAI shape (voice/web path):
curl -sS https://0wlan.com/hub/v1/chat/completions -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"model":"deepseek-v4-flash","stream":false,"messages":[{"role":"user","content":"say OK"}]}' | head -c 300
# Anthropic shape (CLI path):
curl -sS https://0wlan.com/hub/v1/messages -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -H 'anthropic-version: 2023-06-01' \
  -d '{"model":"deepseek-v4-flash","max_tokens":64,"messages":[{"role":"user","content":"say OK"}]}' | head -c 300
# Negative: no token → 401
curl -s -o /dev/null -w '%{http_code}\n' https://0wlan.com/hub/config
```
Expected: `/hub/config` returns the diagnostic JSON with `providers.*` true; both shapes return assistant text; the no-token request returns `401`.

- [ ] **Step 3: Record the first-token latency** (the go/no-go input for voice, sub-project 3): time the OpenAI-shape call above vs a direct `api.deepseek.com` call from the same box, and note both numbers in the runbook.

- [ ] **Step 4: Commit**

```bash
git add docs/runbook/hub-gateway-deploy.md
git commit -m "docs(hub): deploy runbook + end-to-end smoke for the hub gateway" -- docs/runbook/hub-gateway-deploy.md
```

---

## Self-review checklist (run before marking the plan done)

- **Spec coverage:** OpenAI ingress (Task 1) ✓; `/config` (Task 2) ✓; container (Task 3) ✓; compose + Caddy `/hub` strip (Task 4) ✓; Cloudflare Access exclusion + keys-already-present + both-shape smoke + latency probe (Task 5) ✓. The `exp` check the spec mentioned is **already enforced** by `verifyProxyToken` (`proxyJwt.ts:110`) — no task needed; auth is enabled via the container env in Task 3/4.
- **Out of scope (unchanged):** no client repointing (sub-projects 2–5); local `keys.env` NOT emptied; no mode store; no CI-token mint/denylist.
- **Security asserted:** `hub` has **no `ports:`** mapping (internal-only); `JARVIS_PROXY_AUTH_REQUIRED=1`; non-loopback bind is the deliberate `JARVIS_ALLOW_PUBLIC_BIND=1` path; `/config` is auth-gated; Cloudflare Access bypass is scoped to `/hub` only.
- **Type consistency:** `classifyChatCompletionsRequest` returns the `ChatCompletionsRoute` union used in the handler; `buildHubConfig` shape matches its test; handler reuses `executeWithFallback`/`RequestLog`/`checkInboundAuth` verbatim.
