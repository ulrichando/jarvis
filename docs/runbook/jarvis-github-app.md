# JARVIS GitHub App (gh.0wlan.com) — runbook

A **private** GitHub App: install it on a repo, comment `@jarvis <task>` on an
issue or PR, and a VPS service runs jarvis in a sandboxed container and opens
a PR as the App. No workflow file, no PAT, no per-repo secrets.

**Flow:** GitHub → `POST https://gh.0wlan.com/webhook` (HMAC-verified) →
author gate (`OWNER`/`MEMBER`/`COLLABORATOR` ∩ allowlist) → Postgres job queue
→ worker mints a per-installation token → spawns a `jarvis-gh-app` container
(via `docker-proxy`) → `gh-agent::executeTask` clones, runs `jarvis -p`,
commits, pushes, opens the PR.

Code: `src/gh-app/` (service) reusing `src/cli/src/gh-agent/task.ts` (engine)
+ `src/cli/src/gh-action/event.ts` (parse/gate). Plan:
`docs/superpowers/plans/2026-07-03-jarvis-github-app-v1.md`.

## One-time setup

1. **Deploy the service** (VPS, from `src/web/`):

   ```bash
   docker compose build gh-app        # repo-root context; also tags jarvis-gh-app for sandbox spawns
   docker compose up -d gh-app
   docker compose restart caddy       # picks up the gh.0wlan.com route
   ```

2. **Expose the hostname**: add a cloudflared ingress rule for
   `gh.0wlan.com → http://localhost:80`, then scope Cloudflare Access
   **per-path**, not per-hostname:

   - **Exclude ONLY `gh.0wlan.com/webhook` from Cloudflare Access** (a
     bypass/service-auth rule for that one path). GitHub's webhook
     deliveries are server-to-server and cannot pass SSO; the constant-time
     HMAC signature check on `/webhook` is that path's auth layer.
   - **Keep `/setup`, `/setup/callback`, and `/health` BEHIND Cloudflare
     Access.** Do NOT exclude the whole hostname — `/setup/callback`
     converts a manifest code into app credentials, and leaving it open
     lets anyone who can reach the host attempt a credential capture.
     (The service also refuses re-capture once creds exist — 409 — but
     Access is the intended front door.) `/setup/callback` works fine
     behind Access because GitHub redirects your **browser** there, and
     your browser carries the Access session; only `/webhook` is hit
     machine-to-machine.

3. **Create the App** (manifest flow): visit `https://gh.0wlan.com/setup` →
   "Create JARVIS App" → GitHub creates the private app under your account and
   redirects to `/setup/callback`, which captures `{appId, pem,
   webhookSecret}` to the `gh_app_data` volume (`/data/creds.json`, mode 600).
   **Restart the service** to load them: `docker compose restart gh-app`.

   Alternatively (or to override the captured file), put creds in
   `.env.production` — env wins over the file:

   ```
   GH_APP_ID=…
   GH_APP_PRIVATE_KEY=-----BEGIN RSA PRIVATE KEY-----\n…   # literal newlines or \n both work in compose env_file
   GH_APP_WEBHOOK_SECRET=…
   ```

4. **Install on a repo**: GitHub → Settings → Developer settings → GitHub
   Apps → jarvis → Install App → pick the repo(s). (Private app: installable
   only on your own account's repos.)

## Using it

Comment on any issue or PR of an installed repo:

```
@jarvis add a HELLO.md with a one-line greeting
```

- Issue → jarvis pushes a `jarvis/gh-<n>-<ts>` branch and opens a PR.
- PR → jarvis pushes to the PR head branch and comments (untrusted heads —
  fork or non-allowlisted PR author — are refused with a comment).
- New issues whose BODY contains `@jarvis …` also trigger (event `issues`,
  action `opened`). Comment edits/deletions do NOT re-trigger.

## Env vars (all read by `src/gh-app/server.ts`)

| Var | Default | Meaning |
|---|---|---|
| `GH_APP_ID` / `GH_APP_PRIVATE_KEY` / `GH_APP_WEBHOOK_SECRET` | creds file | App identity (env > `/data/creds.json`) |
| `GH_APP_ALLOWLIST` | `ulrichando` | csv of GitHub logins allowed to trigger (ANDed with the OWNER/MEMBER/COLLABORATOR association gate) |
| `GH_APP_TRIGGER` | `@jarvis` | mention trigger |
| `GH_APP_DAILY_CAP` | `20` | max sandbox runs started per day (attempts count) |
| `GH_APP_CONCURRENCY` | `2` | parallel sandbox runs |
| `GH_APP_TIMEOUT_SEC` | `900` | per-job container timeout |
| `GH_APP_PORT` | `8790` | service port (Caddy: `gh.0wlan.com → gh-app:8790`) |
| `GH_APP_BASE_URL` | `https://gh.0wlan.com` | manifest/webhook/callback base |
| `GH_APP_SANDBOX_IMAGE` | `jarvis-gh-app` | image the worker spawns (host daemon tag) |
| `GH_APP_SANDBOX_NETWORK` | — (**required**) | docker network for job containers. **Fail-closed**: when unset/empty the worker REFUSES to spawn (job marked failed) — jobs never run on the default open-egress bridge. MUST be an **egress-restricted** network that can reach only the model proxy + GitHub, NOT arbitrary internet (squid allowlist, same pattern as `/code`) — raw provider keys ride the job env, and prompt-injected repo content could otherwise exfiltrate them |
| `GH_APP_CREDS_PATH` | `/data/creds.json` | captured-creds fallback path |
| `DATABASE_URL` | — (required) | Postgres (`gh_app_jobs` table auto-created) |
| `DOCKER_HOST` | `tcp://docker-proxy:2375` | restricted Docker API for sandbox spawns |
| `ANTHROPIC_API_KEY` / `DEEPSEEK_API_KEY` / `GROQ_API_KEY` / `OPENAI_API_KEY` / `JARVIS_PROVIDER` / `JARVIS_MODEL` | — | passed INTO the sandbox so `jarvis -p` can reach an LLM (strict allowlist — app creds/DB never cross) |

## Security model

- **Webhook**: constant-time HMAC-SHA256 verification of
  `X-Hub-Signature-256` over the raw body (`src/gh-app/sign.ts`); bad sig →
  401; everything else acks 202 so GitHub doesn't retry policy skips.
- **Author gate**: same engine gate as the Action —
  `isAuthorized(association, allowlist, login)`; comment `action=created`
  only; the bot's own comments carry a self-marker and are ignored.
- **Untrusted PR heads**: `executeTask` refuses fork PRs and PRs authored
  outside the allowlist (their code would otherwise run under the agent).
- **`.claude` neutralize**: the target repo's `.claude/` is stashed outside
  the tree while `jarvis -p` runs and restored before `git add`
  (`containerEntry.ts` — same guard as the Action).
- **Token scope**: per-job installation token (expires ~1h, scoped to the
  installation's repos); reaches the sandbox only via env (never argv);
  redacted from all error text; git auth via a container-local
  `url.…x-access-token….insteadOf` rewrite that dies with the container.
- **Sandbox**: throwaway container per job, spawned through the tecnativa
  docker-proxy (no raw socket), `CapDrop=ALL`, `no-new-privileges`, 2 GB /
  512-pid limits, force-removed after run or timeout.
- **Sandbox network (mandatory, fail-closed)**: every job container is
  attached to `GH_APP_SANDBOX_NETWORK`; if it isn't configured the spawn is
  refused and the job fails — there is no fall-open path onto the default
  bridge. The network must be egress-restricted to the model proxy + GitHub
  only. Further hardening (deferred): route the sandbox's LLM calls through
  the model proxy and drop the raw provider keys from the sandbox env
  entirely, so a compromised job has nothing to exfiltrate.

## Operations

- Health: `curl -s https://gh.0wlan.com/health` (or `docker compose ps` —
  the service has a healthcheck).
- Queue: `docker compose exec postgres psql -U jarvis -c "select id, repo, issue_number, status, error from gh_app_jobs order by id desc limit 20"`.
- Logs: `docker compose logs -f gh-app` (webhook decisions, job lifecycle).
- Capped day: jobs stay `queued` and run after midnight UTC; raise
  `GH_APP_DAILY_CAP` + `docker compose up -d gh-app` to run them sooner.

## Rollback

1. `docker compose stop gh-app` — webhook goes dark; GitHub queues + retries
   deliveries for a while, then drops them. Nothing else in the stack
   depends on gh-app.
2. Uninstall the App from the repo (repo → Settings → GitHub Apps) or
   suspend/delete it (account → Developer settings) to stop deliveries at
   the source.
3. Jobs already queued are inert while the service is down; `update
   gh_app_jobs set status='failed', error='rolled back' where
   status='queued'` to drop them permanently.

## Public-listing hardening checklist (DEFERRED — do before ever making the app public)

- [ ] Per-installation (not just global) allowlist + an install-approval step.
- [ ] Rate limit / dedupe per delivery id (`X-GitHub-Delivery`) — replay
      protection beyond the signature.
- [ ] Per-repo daily caps + per-installation spend budgets.
- [ ] Route sandbox LLM calls via the model proxy and DROP raw provider keys
      from the sandbox env (the egress-restricted network is now mandatory —
      this step removes the exfiltration target itself).
- [ ] Webhook secret rotation procedure + dual-secret verify window.
- [ ] Job-row retention/PII policy for task text.

## HELD live E2E (human, outward + live VPS — NOT run during implementation)

1. Register the app against the real VPS via `/setup`.
2. Install on a throwaway repo.
3. Comment `@jarvis add HELLO.md`.
4. Confirm the PR opens as the App; merge or close.
5. Tear down (uninstall from the repo).
