# JARVIS GitHub Action — `@jarvis` as a GitHub-native bot (design)

**Status:** design / awaiting review · **Date:** 2026-07-02 · **Branch:** `cli-feature-unlock`

**Goal:** `@jarvis <task>` in any GitHub issue/PR comment triggers jarvis **instantly
via webhook** (GitHub Actions), on a cloud runner, and opens a PR — the same UX as
`@claude`, but running the *jarvis* CLI (its tools/skills/prompts) with *your*
multi-provider routing.

**Relationship to the poll-runner:** this is a second, independent delivery path.
The poll-runner (`jarvis gh-agent`, P1–P3) stays as the private/local/your-box
option (your models, no GitHub setup). The Action is the cloud/instant/any-repo
option. They **share the execution core** (extract task → run jarvis → commit →
open PR / comment) so behavior stays consistent.

---

## Why an Action (not a hosted App) for v1

GitHub's `issue_comment` / `pull_request_review_comment` / `issues` events **are**
the webhook — they trigger GitHub Actions in seconds. So a composite Action + a
workflow file gives the full "webhook-instant, install-per-repo" experience with
**no server to host**. A one-click GitHub App (auto-installs the workflow + secrets
across repos) is a genuine convenience but a much bigger lift (hosted webhook
receiver, per-install tokens); it is **v2**, explicitly out of scope here. v1
"install" = drop `jarvis.yml` into a repo (or a `gh` one-liner / template repo).

## Model backend — Option 1: self-contained on the runner

The Action installs jarvis on the ephemeral runner and **starts jarvis's own proxy
locally on the runner** (`127.0.0.1:4000`) using your provider keys supplied as
GitHub secrets. `jarvis -p` then runs exactly as it does on your box — full
multi-provider routing, your prompts/tools — but nothing is exposed publicly and
the runner is destroyed after the job.

- The model-backend is an **isolated step** in the composite action, so switching
  to *Anthropic-direct* (set `ANTHROPIC_BASE_URL`/`ANTHROPIC_API_KEY`, skip the
  proxy) is a one-line fallback if a run doesn't need multi-provider or to cut cost.
- Ruled out — **VPS proxy** (Option 2): requires publicly exposing `:4000` and the
  proxy-JWT auth, which is currently desynced/broken (see the 2026-07-02 proxy-auth
  finding); also a single central point of failure. Revisit only after that infra
  is fixed and there's a reason to centralize.

## Components

### 1. `jarvis-action` (composite action, lives in the jarvis repo, pinned by tag/SHA)

Steps, in order:

1. **Guard** (belt-and-suspenders; the workflow `if:` is the primary gate) —
   confirm the event body contains the trigger and the actor is authorized; exit 0
   quietly otherwise.
2. **Install jarvis on the runner.** Preferred: the public installer
   (`curl https://0wlan.com/install.sh | bash` → sha-verified prebuilt binary — see
   `cli-binary-and-web-installer`). Interim until that binary is published: clone
   `ulrichando/jarvis` with a read-scoped token secret and `bun install` (~1–2 min).
   Abstracted behind one `install-jarvis` step so the source can change without
   touching the rest.
3. **Model backend up.** Start the jarvis proxy on `127.0.0.1:4000` with provider
   keys from secrets (Option 1). Health-check before proceeding.
4. **Extract the task** from the event payload (comment body / issue body), stripping
   through the `@jarvis` trigger — reuse the poll-runner's `taskText` (NUL/control
   strip included).
5. **Run** `jarvis -p "<prompt>"` in the checked-out workspace (the runner already
   checked out the repo — no clone needed here, unlike the poll-runner). Enforce a
   timeout; **disable the *target* repo's project hooks/settings** (see Security).
6. **Publish.** `git add -A` → detect changes → commit → push a `jarvis/gh-<n>-<ts>`
   branch → open a PR (issue) / push + comment (PR), via the built-in `GITHUB_TOKEN`.
   No PAT for git ops. Reuse the P2 publish logic.

### 2. `.github/workflows/jarvis.yml` (the template users add to a repo)

- `on:` `issue_comment` (types: created), `pull_request_review_comment` (created),
  `issues` (opened).
- `permissions:` `contents: write`, `pull-requests: write`, `issues: write`.
- One job, guarded by `if:` (see Security), that `uses:` the pinned `jarvis-action`
  and passes provider-key secrets + config inputs (trigger word, allowlist,
  timeout, model).

## Security (load-bearing — this runs untrusted-adjacent input in the cloud)

1. **Author gate.** Run only if the body contains the trigger **and**
   `github.event.*.author_association ∈ {OWNER, MEMBER, COLLABORATOR}`. This is the
   cloud analog of the poll-runner's author allowlist. An optional explicit
   allowlist input narrows further.
2. **Untrusted PR head.** Mirror the P2 lesson: do **not** run bypass-mode jarvis
   inside a fork / cross-repository PR head. Detect fork PRs and refuse (decline
   comment) — running attacker-controlled tree content under an autonomous agent is
   RCE. (GitHub already withholds secrets from fork-PR `pull_request` runs, but
   `issue_comment` runs in the *base* context with secrets, so this gate is on us.)
3. **Ignore the target repo's `.claude/` project config.** Any repo can ship
   `.claude/settings.json` + hooks; a naive `jarvis -p` in the workspace would
   execute them (this is exactly what broke the E2E on our own repo — the Stop hook).
   Run jarvis with project hooks/settings disabled (`JARVIS_SKIP_VERIFY=1` + no
   project settings load) so a repo can't hijack the agent via committed hooks.
4. **Pin** the action by tag→SHA; **scope** provider-key secrets to the repo/org;
   least-privilege `GITHUB_TOKEN`.

## Configuration (workflow inputs / secrets)

- Secrets: provider keys (e.g. `ANTHROPIC_API_KEY`, `GROQ_API_KEY`, …) — whichever
  providers you want the runner's proxy to route to; the interim install token if
  cloning the private repo.
- Inputs: `trigger` (default `@jarvis`), `allowlist` (optional, beyond
  author_association), `timeout-seconds` (default 600), `model` (optional pin).

## Testing

- `actionlint` on the workflow + the action YAML (add to CI).
- Unit-test the extractable logic (task extraction, guard predicate) with `bun test`,
  reusing the poll-runner's tests where shared.
- A `dry-run` input that logs the plan and posts/pushes nothing.
- **Live E2E on a throwaway repo** — outward-facing, **held for the human** (creates
  real PRs), same as the poll-runner's live test.

## Prerequisites & risks

- **Install path:** the clean multi-repo install needs the jarvis binary published
  publicly (`0wlan.com/releases`, CF-Access-excluded — the pending deploy handoff).
  Until then, v1 uses the read-scoped-token clone+build, which spreads a private-repo
  token to each repo — acceptable for your own repos, not for sharing. Publishing the
  binary is the unlock and is tracked separately.
- **Cost:** runner minutes + provider API $ per invocation. The author gate bounds
  who can spend it.
- **Private jarvis repo:** `ulrichando/jarvis` is private; the binary-installer path
  avoids needing repo access on every consumer repo.

## Out of scope (v2+)

- Hosted GitHub App for one-click install across repos.
- 👀-reaction "claimed" feedback, streaming progress comments.
- Publishing the prebuilt binary (separate infra task; this design consumes it).
