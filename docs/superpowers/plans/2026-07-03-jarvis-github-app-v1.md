# JARVIS GitHub App (v1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** a **private** JARVIS GitHub App — install from its page, `@jarvis <task>` fires a webhook to a VPS service that runs jarvis in a sandboxed container and opens a PR as the App. No workflow file, no PAT, no per-repo secrets.

**Architecture:** GitHub App (per-install tokens) → `gh.0wlan.com/webhook` (Bun service) → verify sig → gate → enqueue (Postgres) → worker mints an installation token → spawns a sandboxed jarvis container → `gh-agent::executeTask` opens the PR. Spec: `docs/superpowers/specs/2026-07-03-jarvis-github-app-design.md`.

**Tech Stack:** Bun/TypeScript service, Postgres (jobs/installs), `docker-proxy` (container spawn — reused from `/code`), Caddy + cloudflared. Reuses `src/cli/src/gh-agent/executeTask` + `gh-action/event.ts`.

**Where it lives:** `src/gh-app/` (new top-level Bun service: `server.ts`, `webhook.ts`, `sign.ts`, `manifest.ts`, `token.ts`, `worker.ts`, `jobs.ts`) + `src/gh-app/Dockerfile`; deploy wiring in `src/web/docker-compose.yml` + `src/web/Caddyfile`.

---

## Conventions (all tasks)

- Tests/build with Bun: `bun test src/gh-app/` and `bun build src/gh-app/<f>.ts --no-bundle`.
- **Pathspec-only commits** (repo has 100+ dirty files from parallel sessions): `git add <explicit paths> && git commit -- <same paths>`; verify with `git show --stat HEAD`. Never `git add -A`.
- Injected-deps pattern (mirror `gh-agent`): the worker/webhook take a `deps` object so tests never touch GitHub, Postgres, or Docker.
- No secrets in code; app private key + webhook secret come from env (`GH_APP_ID`, `GH_APP_PRIVATE_KEY`, `GH_APP_WEBHOOK_SECRET`, `GH_APP_ALLOWLIST`).

## Task 0 — PREREQUISITE (not code): land the engine on master

- [ ] Merge **#76** (gh-action: `event.ts` parse + `isAuthorized`) and **#77** (gh-agent: `executeTask`) to `master`. These are USER-GATED (auto-merge to default branch is blocked). Until then the worker can't import the shared brain.
- [ ] Confirm on master: `git cat-file -e origin/master:src/cli/src/gh-agent/task.ts` and `:src/cli/src/gh-action/event.ts`.

If the user prefers not to merge yet, the fallback is to **vendor** `executeTask` + `parseActionEvent`/`isAuthorized` into `src/gh-app/` — but prefer the merge (single source of truth).

---

## Phase 1 — Manifest setup (near-one-click app creation)

### Task 1: The App Manifest + setup page

**Files:** Create `src/gh-app/manifest.ts`, Test `src/gh-app/manifest.test.ts`

- [ ] **Step 1: failing test**
```ts
import { test, expect } from 'bun:test'
import { buildManifest } from './manifest.js'
test('manifest is private with the right perms + events + webhook url', () => {
  const m = buildManifest('https://gh.0wlan.com')
  expect(m.public).toBe(false)
  expect(m.hook_attributes.url).toBe('https://gh.0wlan.com/webhook')
  expect(m.redirect_url).toBe('https://gh.0wlan.com/setup/callback')
  expect(m.default_permissions).toMatchObject({ contents: 'write', pull_requests: 'write', issues: 'write', metadata: 'read' })
  expect(m.default_events).toEqual(expect.arrayContaining(['issue_comment', 'issues', 'pull_request_review_comment']))
})
```
- [ ] **Step 2: run → fail.**
- [ ] **Step 3: implement**
```ts
// src/gh-app/manifest.ts
export type AppManifest = {
  name: string; url: string; public: boolean
  hook_attributes: { url: string; active: boolean }
  redirect_url: string
  default_permissions: Record<string, string>
  default_events: string[]
}
export function buildManifest(base: string): AppManifest {
  return {
    name: 'jarvis', url: base, public: false,
    hook_attributes: { url: `${base}/webhook`, active: true },
    redirect_url: `${base}/setup/callback`,
    default_permissions: { contents: 'write', pull_requests: 'write', issues: 'write', metadata: 'read' },
    default_events: ['issue_comment', 'issues', 'pull_request_review_comment'],
  }
}
// setup page: an HTML form that POSTs `manifest=<json>` to github.com/settings/apps/new
export function setupPageHtml(base: string): string {
  const m = JSON.stringify(buildManifest(base))
  return `<!doctype html><form action="https://github.com/settings/apps/new" method="post">
<input type="hidden" name="manifest" value='${m.replace(/'/g, '&#39;')}'>
<button type="submit">Create JARVIS App</button></form>`
}
```
- [ ] **Step 4: run → pass.** Parse-check.
- [ ] **Step 5: commit** — `feat(gh-app): app manifest + setup page`

### Task 2: Manifest callback → capture credentials

**Files:** Create `src/gh-app/manifest.ts` (add `convertManifestCode`), extend test

- [ ] **Step 1: failing test** — `convertManifestCode(code, deps)` POSTs `/app-manifests/{code}/conversions` and returns `{appId, pem, webhookSecret}`; deps.fetch is stubbed to return a canned conversion payload; assert the URL + that the three fields are extracted.
- [ ] **Step 2: run → fail.**
- [ ] **Step 3: implement** `convertManifestCode(code, { fetch })`: `POST https://api.github.com/app-manifests/${code}/conversions` (Accept: application/vnd.github+json) → JSON `{ id, pem, webhook_secret, client_id, client_secret }` → return normalized. Persist via an injected `saveCreds` (writes to a gitignored `~/.jarvis/gh-app/creds.json` on the VPS, or Postgres).
- [ ] **Step 4: run → pass.**
- [ ] **Step 5: commit** — `feat(gh-app): manifest-code → credentials capture`

---

## Phase 2 — Webhook receiver

### Task 3: Webhook signature verification

**Files:** Create `src/gh-app/sign.ts`, Test `src/gh-app/sign.test.ts`

- [ ] **Step 1: failing test** (known-answer vector)
```ts
import { test, expect } from 'bun:test'
import { verifySignature } from './sign.js'
test('valid X-Hub-Signature-256 passes; tampered fails', () => {
  const secret = 'topsecret', body = '{"a":1}'
  // sha256 hmac known vector computed with the same crypto
  const good = verifySignature(body, sigFor(body, secret), secret)
  expect(good).toBe(true)
  expect(verifySignature(body + 'x', sigFor(body, secret), secret)).toBe(false)
  expect(verifySignature(body, 'sha256=deadbeef', secret)).toBe(false)
})
import { createHmac } from 'node:crypto'
function sigFor(b: string, s: string) { return 'sha256=' + createHmac('sha256', s).update(b).digest('hex') }
```
- [ ] **Step 2: run → fail.**
- [ ] **Step 3: implement** — constant-time compare (`timingSafeEqual`) of `X-Hub-Signature-256` vs `sha256=` + HMAC-SHA256(rawBody, secret). Mirror `proxyJwt.ts`'s HMAC/timing-safe style. Operate on the **raw** body bytes.
- [ ] **Step 4: run → pass.**
- [ ] **Step 5: commit** — `feat(gh-app): webhook HMAC signature verification`

### Task 4: Webhook handler — parse, gate, enqueue, 202

**Files:** Create `src/gh-app/webhook.ts`, `src/gh-app/jobs.ts` (+ tests)

- [ ] **Step 1: failing tests** — `handleWebhook(headers, rawBody, deps)`:
  - bad signature → `401`, nothing enqueued.
  - `@jarvis` from OWNER on an issue → `202` + one job enqueued `{installationId, repo, issueNumber, task, isPR:false}`.
  - authorized-but-no-trigger / unauthorized author → `202` (ack) but **nothing enqueued**.
  Use a stubbed `deps.enqueue` (push to array) + reuse `parseActionEvent`/`isAuthorized` (imported from `gh-action/event.js`); the webhook payload maps to the same shape plus `installation.id`.
- [ ] **Step 2: run → fail.**
- [ ] **Step 3: implement** `webhook.ts` (verify → `parseActionEvent` on the webhook payload → `isAuthorized` + allowlist → `deps.enqueue`) and `jobs.ts` (Postgres `gh_app_jobs` table CRUD: `enqueue`, `claimNext`, `markDone`, `markFailed`, `countToday`; injected `sql` client so tests use an in-memory fake).
- [ ] **Step 4: run → pass.** Parse-check.
- [ ] **Step 5: commit** — `feat(gh-app): webhook handler (verify, gate, enqueue) + jobs table`

---

## Phase 3 — Worker (token + sandboxed run)

### Task 5: Installation access token minting

**Files:** Create `src/gh-app/token.ts` (+ test)

- [ ] **Step 1: failing test** — `appJwt(appId, pem, nowS)` produces a 3-part RS256 JWT with `iss=appId`, `iat=nowS-60`, `exp=nowS+540` (≤10min); `installationToken(installationId, jwt, deps)` POSTs `/app/installations/{id}/access_tokens` with `Authorization: Bearer <jwt>` and returns `{token, expiresAt}` from the stubbed response.
- [ ] **Step 2: run → fail.**
- [ ] **Step 3: implement** — `appJwt` via `node:crypto` `createSign('RSA-SHA256')` over `base64url(header).base64url(payload)` with the PEM; `installationToken` via injected `fetch`.
- [ ] **Step 4: run → pass.**
- [ ] **Step 5: commit** — `feat(gh-app): app JWT + installation token minting`

### Task 6: Worker — claim, sandbox, run, cap

**Files:** Create `src/gh-app/worker.ts` (+ test)

- [ ] **Step 1: failing tests** (all injected deps — no Docker/GitHub):
  - a queued job under the daily cap → mints a token, calls `deps.runInSandbox(job, token)`, `markDone`.
  - daily cap reached (`countToday ≥ cap`) → job deferred, `runInSandbox` NOT called.
  - `runInSandbox` throws/timeout → `markFailed`, error recorded; concurrency never exceeds the cap.
- [ ] **Step 2: run → fail.**
- [ ] **Step 3: implement** `runWorkerOnce(deps)` / `runWorker(deps)`: `claimNext` (respecting concurrency + `countToday < cap`) → `installationToken` → `runInSandbox(job, token)` → `markDone`/`markFailed`. Caps from env (`GH_APP_CONCURRENCY=2`, `GH_APP_TIMEOUT_SEC=900`, `GH_APP_DAILY_CAP=20`).
- [ ] **Step 4: run → pass.**
- [ ] **Step 5: commit** — `feat(gh-app): worker (token, caps, sandbox dispatch)`

### Task 7: Sandbox run entrypoint (reuses executeTask)

**Files:** Create `src/gh-app/runInSandbox.ts` (+ test)

- [ ] **Step 1: failing test** — `runInSandbox(job, token, deps)` spawns a container (stub `deps.spawnContainer`) whose command runs the CLI against the job, with env `GH_TOKEN=<token>` + a git `x-access-token:<token>` credential; assert the spawn is invoked with the clone dir writable, the token in env (NOT in argv/logs), and the timeout applied.
- [ ] **Step 2: run → fail.**
- [ ] **Step 3: implement** — reuse the `/code` container spawn (`src/web/src/lib/bridge/containers.ts` pattern) via `deps.spawnContainer`; the in-container command builds a `Mention` from the job and calls **`gh-agent::executeTask`** with token-authed `gh`/`git` deps (git `http.extraheader` or `x-access-token` remote; `GH_TOKEN` for `gh`). Reuse the `.claude`-neutralize + untrusted-PR-head guards already in `executeTask`.
- [ ] **Step 4: run → pass.** Parse-check.
- [ ] **Step 5: commit** — `feat(gh-app): sandboxed run via executeTask + installation token`

### Task 8: HTTP server wiring

**Files:** Create `src/gh-app/server.ts` (+ smoke test)

- [ ] **Step 1: implement** a Bun HTTP server: `GET /setup` → `setupPageHtml`; `GET /setup/callback` → `convertManifestCode`; `POST /webhook` → `handleWebhook`; `GET /health` → 200. Start the worker loop alongside (or as a second process).
- [ ] **Step 2: smoke** — `bun build src/gh-app/server.ts --no-bundle`; a local `POST /webhook` with a signed test body returns 202 and enqueues (against a fake `sql`).
- [ ] **Step 3: commit** — `feat(gh-app): HTTP server (setup, webhook, health) + worker`

---

## Phase 4 — Deployment

### Task 9: Compose service + Caddy route + runbook

**Files:** Modify `src/web/docker-compose.yml`, `src/web/Caddyfile`; Create `src/gh-app/Dockerfile`, `docs/runbook/jarvis-github-app.md`

- [ ] **Step 1:** `gh-app` service in compose (Bun image built from `src/gh-app/Dockerfile`, depends_on postgres + docker-proxy, env from the VPS secret store, internal port). Caddy: `@ghapp host gh.0wlan.com` → `reverse_proxy @ghapp gh-app:PORT` (mirror the `searx` block); add the cloudflared ingress hostname **excluded from Cloudflare Access** (webhook must be reachable unauthenticated — protected by the signature).
- [ ] **Step 2: validate** — `docker compose -f src/web/docker-compose.yml config` parses; `python3 -c "import yaml; yaml.safe_load(...)"`; Caddyfile `caddy validate` if available.
- [ ] **Step 3: runbook** — `docs/runbook/jarvis-github-app.md`: the one-time setup (visit `gh.0wlan.com/setup` → Create → creds captured), installing on a repo, the env vars, the caps, rollback (stop the service; uninstall the app), and the public-listing hardening checklist (deferred).
- [ ] **Step 4: commit** — `feat(gh-app): deploy (compose service, gh.0wlan.com route, runbook)`
- [ ] **HELD (human, outward + live-VPS):** register the app against the real VPS via `/setup`, install on a throwaway repo, `@jarvis add HELLO.md`, confirm the PR, tear down. Do NOT run as part of implementation.

---

## Self-review notes
- **Spec coverage:** manifest setup (T1–2), signature (T3), gate+enqueue (T4), token (T5), worker+caps (T6), sandboxed executeTask run (T7), server (T8), deploy (T9). All covered.
- **Type consistency:** `Job` shape (`{installationId, repo, issueNumber, task, isPR}`) is identical across `jobs.ts`/`worker.ts`/`runInSandbox.ts`.
- **Prereq flagged:** T0 (merge #76/#77) gates T4/T7's imports.
- **Live-infra caveats (verify during build, not unit-testable here):** the real `docker-proxy` spawn API on the VPS, the `gh.0wlan.com` cloudflared ingress + Access exclusion, and Postgres reachability — these are integration/deploy checks against the live box.
