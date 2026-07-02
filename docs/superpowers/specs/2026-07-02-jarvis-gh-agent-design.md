# JARVIS GitHub Agent (`jarvis gh-agent`) — design

**Date:** 2026-07-02
**Status:** approved (brainstorming) → ready for implementation plan
**Author:** brainstormed with Ulrich

## Context / why

The `jarvis` CLI inherits three dark "runner" command slots from claude-code —
`self-hosted-runner`, `environment-runner`, `daemon` — all gated off and all
**stripped from both the fork and the public donor** (verified 2026-07-02).
`self-hosted-runner` targets Anthropic's internal `SelfHostedRunnerWorkerService`
API, which does not exist for a self-hosted fork. So there is nothing to "turn
on" — a jarvis GitHub agent is a from-scratch build.

Goal (Ulrich's "Path B", from memory `github-agents-claude-bootstrap`): **the
`jarvis` CLI, headless (`-p`), acting as a GitHub agent** — watch a repo, and when
a trusted user writes `@jarvis <task>` on an issue/PR, run jarvis headless in a
sandbox, push a branch, and comment back.

### What the folder review established (fact-check)

- The fork already ships a **Claude-branded** `install-github-app` wizard +
  `pr-comments` command (registered at `commands.ts:298`). It installs Anthropic's
  GitHub App and writes `.github/workflows/claude.yml` referencing
  `anthropics/claude-code-action`, gated on `ANTHROPIC_API_KEY`. Running it gives
  you **@claude**, not a jarvis agent, and requires Anthropic's published Action.
- There is **no `jarvis-action`** (a GitHub Action that runs jarvis in CI), and
  jarvis-in-CI would need model access the fork can't easily give it (the LLM proxy
  is local; `0wlan.com` is behind Cloudflare Access).
- Therefore the **local poll runner** is the pragmatic path for a *jarvis* agent:
  self-contained, no GitHub App, no published Action, no CI model-access problem.
  It reuses the machine's already-authed `gh` CLI (`ulrichando`, `repo`+`workflow`).

The Claude-branded `install-github-app` (branding-rule violation) is noted as a
**separate** follow-up, out of scope here.

## Non-goals

- Not a GitHub Actions integration; no GitHub App; no webhook endpoint (avoids the
  Cloudflare Access carve-out entirely).
- Not auto-merge. The agent proposes (branch + PR/comment); a human merges. Mirrors
  the evolution-loop discipline.
- Not related to JARVIS **evolution rollback** (that's a separate, existing system
  that reverts voice-agent source; unaffected).

## Architecture — small, testable units

New dir `src/cli/src/gh-agent/`:

1. **`config.ts`** — load `~/.jarvis/gh-agent.json` (+ env overrides). Fields:
   `repos: string[]` (`owner/name`), `allowlist: string[]` (GitHub logins allowed
   to trigger; **default `["ulrichando"]`**), `trigger` (default `"@jarvis"`),
   `pollSeconds` (default 45), `model?`, `maxTasksPerHour` (default 6). Pure loader,
   unit-testable.

2. **`gh.ts`** — thin wrappers over `execFileNoThrow('gh', …)` (pattern reused from
   `install-github-app`). `listNewMentions(repo, sinceCursor)`, `postComment`,
   `addReaction`, `createBranchPR`. Uses the `pr-comments` endpoints:
   `/repos/{o}/{r}/issues/comments`, `/pulls/{n}/comments`. Injectable runner so
   tests stub `gh`.

3. **`cursor.ts`** — per-repo cursor at `~/.jarvis/gh-agent/<owner>__<repo>.cursor`
   (ISO timestamp / last comment id). `read`, `advance`. Guarantees a restart never
   re-runs old mentions. Unit-testable.

4. **`task.ts`** — execute one mention:
   - create a throwaway **git worktree** (never the live checkout),
   - run `jarvis -p "<task>"` headless (bypass, bounded), capture output + `git diff`,
   - push a branch `jarvis/gh-<issue>-<shortsha>`, then `gh pr comment`/`gh pr create`,
   - tear down the worktree. Supports `--dry-run` (plan + comment intent, no push).

5. **`main.ts`** — the loop. `runGhAgent({ once, watch, dryRun, repo? })`: load config,
   poll each repo, gate by allowlist, dispatch `task.ts` for each new mention, advance
   cursor, sleep `pollSeconds` (watch) or exit (once). Ignores mentions authored by
   the agent's own account (no self-trigger loop) and enforces `maxTasksPerHour`.

6. **Command registration** — `jarvis gh-agent [--once|--watch] [--repo o/n] [--dry-run]`
   in `main.tsx` (source-run; no compile needed). Native command; the dead
   `self-hosted-runner` slot is left as-is.

### Data flow

```
@jarvis <task> comment (gh api)
  → cursor: is it new?          (cursor.ts)
  → allowlist: trusted author?  (config.ts)   ── else ignore + log
  → claim: add 👀 reaction      (gh.ts)        ── idempotency marker
  → worktree + jarvis -p        (task.ts)
  → push branch + PR comment    (gh.ts)
  → advance cursor              (cursor.ts)
```

## Safety (load-bearing, fail closed)

- **Author allowlist** is the #1 guard: a public issue comment triggering `jarvis -p`
  is a prompt-injection → arbitrary-execution vector. Untrusted authors are ignored
  and logged. Default allowlist = `["ulrichando"]`.
- **Sandbox**: work happens only in a throwaway worktree; the live checkout is never
  touched. No secrets beyond the already-authed `gh`.
- **Never auto-merge**: push branch + comment/PR only.
- **Self-trigger guard**: skip comments authored by the runner's own `gh` account.
- **Rate cap**: `maxTasksPerHour` (default 6); excess deferred + logged.
- **Idempotency**: 👀-reaction claim + persisted cursor so restarts/overlaps never
  double-run a mention.

## Phasing (each ships safe + testable)

- **P1 — skeleton:** `config` + `gh` (read-only) + `cursor` + `main --once --dry-run`.
  Polls, gates, and *comments the intended plan* — no worktree, no push. Fully safe;
  proves the loop end-to-end.
- **P2 — execution:** `task.ts` worktree + `jarvis -p` + branch push + PR comment.
  `--once` real run.
- **P3 — daemon:** `--watch` + optional `jarvis-gh-agent.service` (systemd `--user`,
  mirrors existing units) + rate caps + reaction-claim.

## Testing

- Unit (assert-based, stubbed `gh` runner): `cursor` advance/no-replay; `config`
  defaults + allowlist gating (trusted vs untrusted author); mention parsing.
- `--dry-run` live smoke: post `@jarvis <task>` on a throwaway issue in
  `ulrichando/jarvis`, run `jarvis gh-agent --once --dry-run`, assert it comments a
  plan and does not push.
- P2 end-to-end (manual): same, real `--once`, assert branch pushed + PR comment,
  worktree cleaned.

## Reuse (don't reinvent)

- `execFileNoThrow('gh', …)` + repo-parse helpers — from `install-github-app`.
- `gh api` comment endpoints — from `pr-comments`.
- systemd `--user` unit shape — from existing `jarvis-*.service` units.
- headless invocation — the proven `jarvis -p` contract (`cli/print.ts`).

## Open questions (resolved)

- Delivery: **polling** (not webhook) — no inbound exposure, no CF Access change.
- Backend: **local**, reusing authed `gh` — not Actions, not the fork web.
- Command name: **`jarvis gh-agent`** (native) — the `self-hosted-runner` slot stays dark.
