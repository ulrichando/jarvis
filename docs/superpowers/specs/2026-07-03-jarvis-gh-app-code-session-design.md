# JARVIS gh-app × /code — run each `@jarvis-gh-bot` job as a watchable `/code` session (design)

**Status:** design / awaiting review · **Date:** 2026-07-03

**Goal:** `@jarvis-gh-bot <task>` runs the job as a real jarvis **`/code` session** — isolated, **watchable + steerable** at `0wlan.com/code/<id>`, **persistent** (history preserved), and **linked from the GitHub thread + PR** — instead of a throwaway headless sandbox. This is the self-hosted, GitHub-triggered equivalent of **claude.ai/code**.

## Fact-check: the claude.ai/code model we're matching (from Anthropic docs)

- **One isolated session (fresh VM) per task**, cloned repo.
- **GitHub proxy for auth — the token never enters the sandbox** (scoped credential); built-in GitHub tools create the PR / post comments through it.
- **Real-time progress + steering**; sessions **persist** across browser close, resumable, `--remote`/`--teleport` move web↔terminal.
- **Session link stamped back**: a transcript URL goes into the **PR body** + a **`Claude-Session:` git trailer**, so a reviewer opens the exact run from the PR.
- Auto-fix (respond to CI failures / review comments) is an adjacent webhook feature — out of scope for v1.

**JARVIS is aligned:** `/code` already runs isolated container sessions behind a **scoped-credential git proxy** (`web-scoped-credential-git-proxy`) — the hard security part is done.

## Architecture

```
@jarvis-gh-bot webhook → gh-app worker
   → POST /api/bridge/v1/sessions  (internal, service-token auth)
        { externalRepo: "ulrichando/maxrun", installationToken, task, autonomous:true }
   → /code creates an isolated session: clone maxrun via the scoped git proxy
     (the App installation token lives in the PROXY, never in the container)
   → worker runs jarvis on the task, streamed → watchable at 0wlan.com/code/<id>
   → opens the PR via POST /api/bridge/v1/sessions/{id}/pr, stamping the session
     URL into the PR body + a Jarvis-Session commit trailer
   → gh-app edits the tracking comment: 👀 → "working — watch: <session link>"
     → "✅ Opened #<pr> · session: <link>"
```

The throwaway-sandbox path (`runInSandbox`) is **replaced** by the session path (kept behind a flag for one release as a fallback).

## The three adaptations (the real work)

1. **Cross-service call, gh-app → web session API.** gh-app is a separate Bun service; it calls `POST /api/bridge/v1/sessions` over the internal docker network, authenticated with a **service token** (`GH_APP_BRIDGE_TOKEN`, shared via env; verified like the existing bridge auth). Add an internal "create session for a bot job" entry that doesn't require a browser session.

2. **External-repo + App-token session.** `/code` today clones the *connecting user's* repos. Add an **external-repo path**: the session-create accepts `{ externalRepo, installationToken }` and wires the token into the **scoped git proxy** for that repo only (repo-scoped, 1-hour, like the App token already is) so it clones/pushes `maxrun` — **token stays in the proxy, never in the container** (the claude.ai/code invariant, which the existing `/code` git-proxy already enforces).

3. **Autonomous-but-watchable run.** The session's `WorkerSpec.cmd` seeds the task and runs jarvis so it **does the work and opens the PR on its own**, but streamed to the session transcript so you **watch live** and can **open the session to take over** (steer). Reuse the session's existing PR endpoint + streaming; no new interactive protocol.

## Security (carries the claude.ai/code invariants + our existing guards)

- **Token never in the container** — via the scoped git proxy (existing `/code` mechanism); the App installation token is repo-scoped + 1-hour.
- **Author-association gate + untrusted-PR-head refusal** — unchanged (from the gh-app).
- **`.claude` hooks:** claude.ai/code *does* load the repo's `.claude` (their sandbox, their risk). For a bot acting on the owner's own repos this is lower-risk, but to stay safe v1 keeps the **neutralize-`.claude`** behavior (configurable later). Revisit per-repo.
- Per-run resource caps + concurrency (the session system's limits; the App's daily cap still gates job count).

## Linking (the "history of the chat")

- The session **transcript URL** (`0wlan.com/code/<id>`) is stamped into: the **PR body**, a **`Jarvis-Session:` commit trailer**, and the gh-app **tracking comment** ("watch it live" while running, "session: <link>" when done). One click from GitHub → the watchable run.

## Reuse (what already exists)

`api/bridge/v1/sessions` (create) + `/sessions/{id}/pr` + `/diff` + `/pr-status` + `/events`; `bridge/containers.ts` (container/worker spawn); `WorkerSpec {env,cmd,workdir}`; the scoped-credential git proxy; the gh-app webhook/gate/feedback layer (already live).

## Testing

- Unit (hermetic): the gh-app→session client (payload, service-token), the external-repo/token wiring, the session-URL stamping, the tracking-comment link updates.
- Integration on the box: a bot job creates a real session, clones maxrun via the proxy, runs, opens a PR with the session link, and the session is watchable at `0wlan.com/code/<id>`. (Live, but on the throwaway `maxrun` — held for a deliberate run.)

## Out of scope (v1)

- Auto-fix (CI-failure / review-comment webhooks) — later.
- Full interactive "waits-for-you" mode — v1 is autonomous+watchable; take-over = open the session.
- `--teleport` to a local terminal for bot jobs.

## Open risks

- `src/web` is **stock Next.js 16.2.6 + React 19.2.4** (from `vercel/next.js`, not a fork). The `AGENTS.md` "NOT the Next.js you know" banner is **Next.js 16's own auto-generated agent-rule** warning that v16 has breaking API changes vs. older versions (which is what's in the implementer's training data). Mitigation: **read the bundled docs at `node_modules/next/dist/docs/` (01-app, index.md) before writing any route/server-component code.** Bounded risk — the docs ship in-repo.
- The `/code` clone/auth path assumes user workspaces; the external-repo adaptation is the riskiest change — must not regress normal `/code`.
- Live-VPS-only checks (proxy wiring, the session actually watchable) — verified during build against the box.

## v2 amendment — installation-token refresh (2026-07-04)

v1 minted the ~1h App installation token once at dispatch and stored it raw in
the session's container meta; any session outliving it (steered runs, late PR
opens, follow-up pushes) hit GitHub 401s ("git push failed … token may have
expired"). v2 makes the token refreshable at every use site while keeping the
v1 trust shape (the web never holds the App private key):

- **gh-app `POST /internal/mint-token`** (`server.ts`): body `{ repo }`
  (owner/name), auth `x-gh-app-token: GH_APP_BRIDGE_TOKEN` — the SAME shared
  service token the web's dispatch route verifies, direction inverted; no new
  secret. Resolves the repo's installation, mints a repo-scoped token
  (`token.ts::installationTokenForRepo`), returns `{ token, expiresAt }`.
  Route is inert (404) without creds + bridge token.
- **web `lib/bridge/gh-app-token.ts::freshInstallationToken`**: called at the
  four token use sites (git proxy, PR open, PR status, merge). Returns the
  stored token while it has >5 min left; otherwise re-mints via the gh-app and
  persists `{ installationToken, installationTokenExpiresAt }` back into the
  session meta. Dispatch-time meta records no expiry → the first use costs one
  mint round-trip, then the persisted expiry gates. Any refresh failure falls
  back to the stored token — exact v1 behavior, the GitHub 401 surfaces
  upstream.
- **Env (web service):** `GH_APP_INTERNAL_URL=http://gh-app:8790` (compose
  service name; both services already share `.env.production`). Unset →
  refresh disabled, v1 behavior byte-for-byte.
