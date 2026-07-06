# JARVIS GitHub App — install-from-a-page `@jarvis` bot (design)

**Status:** design / awaiting review · **Date:** 2026-07-03

**Goal:** `@jarvis <task>` on any repo where the **JARVIS GitHub App** is installed triggers jarvis **instantly** (webhook), runs it **on your VPS**, and opens a PR — installed with one click from the app's page (no workflow file, no PAT, no per-repo secrets), the Codecov experience.

**Architecture (one line):** a GitHub App (identity + per-install tokens) → webhooks to a small **VPS service** → verify + gate + enqueue → a worker mints an installation token and runs jarvis in a **sandboxed container** (reusing the `/code` container infra + `gh-agent`'s `executeTask`) → opens the PR as the App.

**Tech:** Bun/TypeScript service, Postgres (job + install state, already in the stack), the `docker-proxy` container-spawner (already in the stack), Caddy route behind cloudflared. Reuses `src/cli/src/gh-agent/executeTask` as the brain.

---

## Scope: PRIVATE app for v1 (fact-check constraint)

Because the VPS *runs the coding job*, a **public** install would let strangers execute code on your box (unbounded compute/cost + attack surface). So v1 is a **private GitHub App**: only you install it, on your own repos. Same one-click install; it just can't be installed by others. Public/Marketplace is a **separate hardened milestone** (§Out of scope), not v1.

Being private does **not** reduce install ease — you still click Install → pick repos → done.

## Components & data flow

```
@jarvis comment
  → GitHub webhook  →  [VPS: /webhook]  verify sig → gate → enqueue (respond 202 <10s)
                                              ↓
                        [VPS: worker]  mint installation token
                                              ↓
                        [docker-proxy]  spawn sandboxed jarvis container
                                              ↓
                        executeTask(repo, mention, deps=token-authed gh/git)
                                              ↓
                        clone → jarvis -p → open PR  (as the App)
```

### A. The App (registered once, via manifest)
A GitHub App owned by your account, **`public: false`**. Permissions: `contents: write`, `pull_requests: write`, `issues: write`, `metadata: read`. Events: `issue_comment`, `issues`, `pull_request_review_comment`. Webhook URL → `https://gh.0wlan.com/webhook` + a webhook secret. Ships a private key used to mint installation tokens.

### B. Setup flow (near-one-click owner setup)
A tiny VPS page `GET gh.0wlan.com/setup` renders a form that POSTs a **GitHub App Manifest** (all settings pre-filled) to `github.com/settings/apps/new`. You click **"Create JARVIS App"**; GitHub redirects to `gh.0wlan.com/setup/callback?code=…`; the service calls `POST /app-manifests/{code}/conversions` and **auto-captures** the app id, private key (PEM), webhook secret, and client id/secret, storing them in VPS state. ~30s, once ever. (Confirmed real GitHub flow.)

### C. Webhook receiver (`POST /webhook`)
1. **Verify** `X-Hub-Signature-256` = HMAC-SHA256(body, webhook_secret) — reuse the repo's existing HMAC (`proxyJwt.ts` pattern). Reject on mismatch.
2. **Parse** the event → `{trigger?, task, author, association, repo, issueNumber, isPR, installationId}` (reuse `gh-action/event.ts::parseActionEvent` shape).
3. **Gate:** trigger present AND `author_association ∈ {OWNER,MEMBER,COLLABORATOR}` AND the installation/owner is allowlisted (private: your account).
4. **Enqueue** the job, **respond `202` immediately** (GitHub's 10s SLA is hard — confirmed; long work must be async).

### D. Queue + worker
- **Queue:** a Postgres `gh_app_jobs` table (status: queued/running/done/failed) — avoids adding Redis; Postgres is already in the stack.
- **Worker:** claims queued jobs under a **concurrency cap** (e.g. 2). Per job:
  1. Mint an **installation access token** (app JWT [RS256, `iss`=app id, `exp`≤10min] → `POST /app/installations/{installationId}/access_tokens` → 1-hour token — confirmed).
  2. **Spawn a sandboxed jarvis container** (reuse the `/code` `docker-proxy` spawn) with the token + task + a **per-run timeout** (e.g. 15 min) and resource caps.
  3. Inside: `GH_TOKEN`=token + git credential `x-access-token:token`, then call **`gh-agent::executeTask`** (clone → `jarvis -p` → open PR / push+comment). This is the already-tested brain; the App just supplies token-authed `gh`/`git` deps.
- **Caps:** concurrency, per-run timeout, **daily cap** (e.g. 20, like the automod cap), per-installation rate limit.

## Security (load-bearing)

- **Private app + install allowlist** — only your account installs it; the worker also refuses installations/owners not on the allowlist.
- **Webhook signature** verified before any work (drops spoofed events).
- **Author gate** (`OWNER/MEMBER/COLLABORATOR`) — a comment from a random drive-by never triggers a run.
- **Untrusted PR-head refusal** + **`.claude` neutralize** — same guards as the gh-action engine (don't run bypass-jarvis on fork/attacker tree content or its committed hooks).
- **Per-run container sandbox** (the `/code` isolation) + concurrency/timeout/daily caps — bounds the blast radius and cost of any single run.
- **Installation tokens** are per-install, least-privilege, 1-hour — never a broad PAT; the App private key + webhook secret live only in VPS env/secret storage.

## Deployment

A new **`gh-app` service** in `src/web/docker-compose.yml` (Bun image; imports `gh-agent` logic), reachable via a Caddy route `@ghapp host gh.0wlan.com → gh-app:PORT` (same pattern as `searx`), exposed through the existing cloudflared tunnel, **excluded from Cloudflare Access** (GitHub must reach `/webhook` unauthenticated — it's protected by the signature instead, like `/install.sh`). Model access = the same `keys.env` + `:4000` proxy the `/code` containers already use.

## Install / use flow (the payoff)
1. One-time: `gh.0wlan.com/setup` → "Create JARVIS App" → done (~30s).
2. Per repo: open the app's page → **Install** → pick the repo. One click.
3. Comment `@jarvis add a file X` on an issue → a PR appears, opened by jarvis.

## Testing
- Unit: signature verification, the gate, event parse, token-mint request shaping, the worker's job lifecycle (all with injected deps — no live GitHub).
- Integration: a local webhook POST with a signed test payload → asserts a job is enqueued and the worker would spawn the right container/executeTask call (stubbed).
- Live E2E (held, outward): register the app against the real VPS, install on a throwaway repo, `@jarvis`, confirm the PR.

## Out of scope (later milestones)
- **Public / Marketplace listing** — needs strong multi-tenant isolation, abuse/rate protection, and cost controls before strangers can run jobs on the VPS.
- **A hosted "install" landing page** beyond the minimal setup route.
- The stale donor `install-github-app` (Claude's App+Actions) — separate cleanup; unrelated.

## Open risks / residuals
- **Live VPS health** — the `/code` container infra exists in code + compose; confirm `docker-proxy` + `keys.env` + the `:4000` proxy are actually up on the box during build (couldn't verify remotely).
- **Resource contention** — the 8GB VPS also runs web + `/code` + computer-use + postgres + searxng; the concurrency cap must be conservative.
- **The one irreducibly-manual step** — creating the App on GitHub is your account action (the manifest flow makes it ~one click, but GitHub requires you to click "Create").
- **Build dependency on #76/#77** — the worker reuses `gh-agent::executeTask` (PR #77) and the event-parse shape (`gh-action/event.ts`, PR #76), neither of which is on `master` yet. The plan must either sequence those merges first or vendor the shared functions; otherwise the worker reimplements clone→`jarvis -p`→PR from scratch.
