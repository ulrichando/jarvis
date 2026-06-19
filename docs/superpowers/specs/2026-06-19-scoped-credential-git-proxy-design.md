# Scoped-credential git proxy for `/code` container sessions — design

**Date:** 2026-06-19
**Status:** Approved (design validated against Anthropic's published Claude Code sandbox architecture; user delegated sign-off — "research and find out")
**Scope:** `src/web` only. Container `/code` sessions only.

## Problem

Today every `/code` container session receives the user's **real GitHub Personal
Access Token** in three places (`src/web/src/lib/bridge/containers.ts`):

1. The clone URL argv — `https://x-access-token:<PAT>@github.com/<repo>.git` (`:390`,
   `:413`).
2. A persistent credential file — `configureGitCreds` writes
   `https://x-access-token:<PAT>@github.com` into `$HOME/.git-credentials` (`:357`,
   `:364`).
3. Environment variables — `GH_TOKEN` / `GITHUB_TOKEN` set to the real PAT (`:628`).

The PAT in `~/.jarvis/connectors.json` is a single long-lived token, typically broad
`repo` scope across **all** the user's repositories (`src/web/src/lib/connectors/github.ts`).
Consequently a prompt-injected agent, or a malicious dependency `postinstall` running in
**any** session, can read `$GITHUB_TOKEN` (or `~/.git-credentials`) and push to / read
from **every** repo the user owns, create releases, delete branches, etc. The squid
egress proxy already in `containers.ts` is **domain-level only** — it does not scope the
credential, repo, operation, or branch; and on isolated levels `github.com` is in the
default allowlist, so the real token flows straight through.

## Goal

Replicate claude.ai/code's **scoped-credential git proxy**: the real PAT never enters
the container. Inside the container, git authenticates to a host-side proxy with a
**per-session capability token**; the proxy verifies the credential, enforces that the
operation targets one of the session's repos with an allowed operation (fetch/push),
then attaches the real PAT before forwarding to GitHub.

### Reference architecture (validated)

From Anthropic's engineering writeup (sources below): *"Inside the sandbox, the git
client authenticates to this service with a custom-built scoped credential. The proxy
verifies this credential and the contents of the git interaction (e.g. ensuring it is
only pushing to the configured branch), then attaches the right authentication token
before sending the request to GitHub."* The proxy validates three things — credential,
repository, branch — and *"sensitive credentials (such as git credentials or signing
keys) are never inside the sandbox."*

This design implements **credential + repo + operation** enforcement. Branch
enforcement (the third claude.ai check) is the deferred Phase-2 increment (see Out of
Scope); GitHub branch protection is the backstop in the interim.

### Non-goals

- **Branch-namespace / force-push enforcement** — deferred; needs `git-receive-pack`
  packfile ref-line parsing. Repo+op enforcement reads everything it needs from the URL.
- **The local-machine worker** (`claude_code_repl` on Moon) — runs as the user on their
  own box with their own git config; there is no sandbox to escape. Untouched.
- **A GitHub REST (`api.github.com`) proxy** — not built. REST actions (open PR, merge,
  status, comments) stay host-side functions using the real PAT, where they already live.
- **Migrating off PATs to a GitHub App** — out of scope; the proxy works with the
  existing one-paste PAT.

## Architecture

The proxy is a **new bridge route on the web app**, not a new sidecar process — it reuses
plumbing that already exists: the web app already holds the PAT (`getGithubToken()`),
already maps session → repo, is already reachable from the container over
`host.docker.internal` (the existing callback channel), and already issues a per-session
token. JARVIS uses Docker network isolation rather than claude.ai's bubblewrap +
unix-domain-socket, but the credential-proxy principle is identical.

### Components

**New — `src/web/src/lib/bridge/git-proxy.ts`** (the testable policy + forwarding layer;
keeps the route thin):

- `parseGitRequest(pathSegments: string[], search: URLSearchParams): GitRequest | null`
  — extracts `{ owner, repo, service, kind }` from the URL.
  - `kind: "info-refs"` when the path ends `/info/refs` (service from `?service=`).
  - `kind: "service"` when the path ends `/git-upload-pack` or `/git-receive-pack`
    (service from the last segment).
  - `service: "git-upload-pack"` (fetch) | `"git-receive-pack"` (push).
  - `owner`/`repo` from the two segments preceding the git suffix; `repo` strips a
    trailing `.git`.
  - Returns `null` for any path that isn't one of these two shapes (defense-in-depth: an
    unrecognized path is rejected, not forwarded).
- `assertRepoAllowed(allowedRepos: string[], owner: string, repo: string): boolean` —
  case-insensitive membership test against the session's repo set (`owner/repo`).
- `forwardToGithub(req: Request, target: GitRequest, pat: string): Promise<Response>` —
  builds `https://github.com/<owner>/<repo>.git/...` (preserving `?service=` and the
  `git-upload-pack`/`git-receive-pack` suffix), injects
  `Authorization: Basic base64("x-access-token:" + pat)`, **forwards the `Git-Protocol`,
  `Content-Type`, and `Accept` headers verbatim** (v2 + content-type correctness), streams
  the request body (`body: req.body, duplex: "half"`, buffer fallback), and returns the
  upstream `Response` **streamed** (`new Response(upstream.body, { status, headers })`)
  with the upstream `Content-Type` preserved and any inbound credential headers stripped.
- `const GITHUB_GIT_BASE = "https://github.com"`.

**New — `src/web/src/app/api/bridge/v1/code/sessions/[sessionId]/git/[...path]/route.ts`**
(GET + POST handlers; thin):

1. Read `sessionId` (path) + the Basic-auth password (the **cap token**).
2. Resolve `findSessionByGitCapToken(store, capToken)`; require it exists, equals the
   path's `sessionId`, and the session is not archived. Else **401**.
3. `parseGitRequest(...)`; `null` → **400**.
4. `assertRepoAllowed(getSessionGitScope(session), owner, repo)`; fail → **403** + an
   audit `session_event` (a scope violation is a possible-injection signal).
5. `getGithubToken()`; missing → **503** ("GitHub not connected").
6. `forwardToGithub(...)`; stream the result back. Upstream non-2xx passes through.
7. Audit every proxied op: `{ session, repo, service, result }` (claude.ai-style trail).

**Modified — `src/web/src/lib/bridge/store.ts`:**

- Persist a per-session **`git_cap_token`** (distinct from the worker `sit_` token, so a
  leak of one does not grant the other) and the session's allowed-repo set.
- `setGitCapToken(store, sessionId, token)`, `findSessionByGitCapToken(store, token)`,
  `getSessionGitScope(session): string[]` (primary repo + extraRepos, read from
  `container_json` which already carries `repo`; extend it with `extraRepos`).

**Modified — `src/web/src/lib/bridge/containers.ts`:**

- `configureGitCreds` → `configureGitProxy`: write `.git-credentials` with
  `http://x-access-token:<capToken>@<proxyHost>` (the **proxy** host — `childBaseUrl`'s
  host:port — never github.com), keep `credential.helper store`, keep user.name/email +
  `safe.directory`. **No real PAT written anywhere in the container.**
- Clone via the proxy URL —
  `${childBaseUrl}/api/bridge/v1/code/sessions/<sid>/git/<owner>/<repo>.git` — for the
  primary and each extra repo. Leave the remote pointed at the proxy URL (auth via the
  credential helper). The clone argv no longer contains a token.
- Generate + persist the cap token (`setGitCapToken`) and the repo scope before clone.
- **Drop `GH_TOKEN` / `GITHUB_TOKEN`** from `childEnv` (`:628`).
- Remove `.github.com` from the squid `DEFAULT_ALLOW` so isolated sessions **must** use
  the proxy for GitHub (the proxy path rides `host.docker.internal`, already in
  `NO_PROXY`, so it bypasses squid). `.githubusercontent.com` (read-only CDN, no
  credential) may remain. Documented consequence: setup scripts on isolated levels that
  need `api.github.com`/`codeload.github.com` directly won't reach them — git goes
  through the proxy; REST is a host-side action.
- Drop the "run `gh pr create` yourself" clause from the identity prompt; replace with
  "git is wired through a secure proxy — push your branch; the PR opens from the panel."

**Modified — `src/web/src/lib/bridge/containers.ts` PR/merge path + `connectors/github.ts`:**

- `createContainerPR` / `mergeContainerPR`: keep the in-container **push** (now via the
  proxy); move the **PR open / merge** to **host-side REST** using the real PAT. Add
  `openPullRequest(repo, head, base, title, body)` and `mergePullRequest(repo, number)`
  to `github.ts` (mirroring the existing `postPrComment` / `githubPrStatus` patterns).
  This removes the last need for the real token inside the container.

### Data flow (a push)

1. Agent runs `git push origin jarvis/foo`.
2. Git → `GET {childBaseUrl}/api/bridge/v1/code/sessions/<sid>/git/<owner>/<repo>.git/info/refs?service=git-receive-pack`,
   Basic-auth password = **cap token**, header `Git-Protocol: version=2`.
3. Proxy: cap token → session (matches path `sessionId`, not archived); `<owner>/<repo>`
   ∈ scope; `git-receive-pack` is a push (allowed for the session's repos). Forward to
   `https://github.com/<owner>/<repo>.git/info/refs?service=git-receive-pack` with
   `Authorization: Basic x-access-token:<PAT>` + the `Git-Protocol` header; stream back.
4. Git → `POST .../git-receive-pack` (packfile body); proxy streams it upstream with the
   real auth + verbatim `Content-Type`; streams the response back.

The real PAT is attached **only on the host, only on the upstream request**. The
container ever sees: a cap token + a proxy URL. `owner/repo` + operation are read from
the **URL** — no packfile parsing. Fetch/clone is identical with `git-upload-pack`.

### Token model

- **Cap token** — `git_<base64url(24)>`, per session, in `.git-credentials` for the proxy
  host only. Authenticates git ops; useless outside the proxy (maps to a session row that
  names the allowed repos). Sent as the Basic-auth **password** (username conventionally
  `x-access-token`, ignored).
- **Real PAT** — host-only (`~/.jarvis/connectors.json`); attached by the proxy to
  upstream requests; never written into the container.

## Error handling + audit

- Missing/invalid cap token, or token's session ≠ path session, or archived → **401**.
- Unrecognized git path → **400**.
- Repo not in scope → **403** + audit `session_event` (possible injection).
- PAT missing host-side → **503** ("GitHub not connected — reconnect in Settings").
- Upstream GitHub error → status passes through.
- Every proxied op logs `{session, repo, service, result}`.

## Testing

`src/web/tests/bridge/`, vitest, upstream `fetch` mocked (no real github.com):

- **`git-proxy.test.ts`** — `parseGitRequest` for info/refs + service paths, v1 and v2,
  `.git` suffix handling, and `null` on junk paths; `assertRepoAllowed` accept/reject
  (incl. case-insensitivity + an out-of-scope repo); `forwardToGithub` injects
  `Authorization: Basic x-access-token:<PAT>` and forwards `Git-Protocol`/`Content-Type`;
  route returns 401 (bad cap token), 403 (out-of-scope repo) + emits an audit event,
  503 (no PAT), and streams a 200 with upstream content-type on the happy path.
- **`containers-git-proxy.test.ts`** — `configureGitProxy` writes the cap token (and
  **not** the PAT) into `.git-credentials`; `childEnv` contains **no** `GH_TOKEN`/
  `GITHUB_TOKEN`; the clone uses the proxy URL (no token in argv); the session's git scope
  + cap token are persisted.
- Existing container tests stay green (launch flow now clones via the proxy URL).

## Out of scope (stated, not silently dropped)

- **Branch-namespace / force-push enforcement** — the third claude.ai proxy check.
  Deferred; would parse `git-receive-pack` ref-update lines to allow only `jarvis/*` and
  block force-push to the default branch. Brings full claude.ai parity. Backstop now:
  GitHub branch protection.
- **`full`-network caveat** — on the default `--network=host` level the container can
  still reach github.com directly, but it now holds **no GitHub credential**, so only
  anonymous (public read) is possible without the proxy. Full containment (proxy = sole
  GitHub path) is realized on isolated network levels. Recommend isolated for sessions
  that rely on the proxy's repo-scoping as a hard boundary.
- **Local-machine worker**, **REST/`gh` proxy**, **GitHub App migration** — as above.

## Sources

- Anthropic — *Making Claude Code more secure and autonomous with sandboxing*
  (https://www.anthropic.com/engineering/claude-code-sandboxing) — the git credential
  proxy (credential/repo/branch checks; token never in sandbox; unix-socket egress).
- `anthropic-experimental/sandbox-runtime`
  (https://github.com/anthropic-experimental/sandbox-runtime) — OS-level (bubblewrap/
  seatbelt), network namespace removed, all egress via proxies.
- Git smart-HTTP protocol (https://git-scm.com/docs/gitprotocol-http,
  https://git-scm.com/docs/protocol-v2) — `info/refs?service=`, the
  `git-upload-pack`/`git-receive-pack` endpoints (unchanged in v2), the `Git-Protocol`
  header, and the content-type requirement.
