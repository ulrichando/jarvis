# jarvis GitHub Action — `@jarvis` on any repo

**Status:** implemented 2026-07-02; live E2E on a throwaway repo pending (human-run).
**Plan:** `docs/superpowers/plans/2026-07-02-jarvis-github-action-v1.md`.

Comment `@jarvis <task>` on an issue or PR (or open an issue whose body
mentions `@jarvis`) and a GitHub Actions workflow runs the jarvis CLI on
the runner against the checked-out repo, then opens a PR (issue) or
pushes to the PR branch (PR comment). Event-driven — fires instantly on
the webhook, no polling.

It **coexists with the poll-runner** (`jarvis gh-agent`, module
`src/cli/src/gh-agent/`): gh-agent polls from your box on a timer with
your local `gh` auth; gh-action runs *on GitHub's runner* per event.
Both filter the same `<!-- jarvis-gh-agent -->` self-marker, so neither
ever reacts to the other's (or its own) posts.

## Enable on a repo (3 steps)

1. **Copy the workflow** — `.github/workflows/jarvis.yml` from this repo
   into the target repo (same path). Commit to the default branch
   (`issue_comment` workflows only run from the default branch).
2. **Add repo secrets** (Settings → Secrets and variables → Actions):
   - `JARVIS_CLI_TOKEN` — fine-grained PAT with **read-only Contents**
     scope on `ulrichando/jarvis`, used only to clone the CLI onto the
     runner. Nothing else.
   - `ANTHROPIC_API_KEY` — primary model provider.
   - `DEEPSEEK_API_KEY` — optional fallback provider. Add any other
     provider keys the same way (they flow to `bin/jarvis` via the
     workflow's `env:` block; the CLI auto-starts its proxy from them —
     self-contained, no callback to your box).
3. **Trigger it** — comment `@jarvis <task>` on an issue. Watch the run
   under the repo's Actions tab; it ends with a PR link.

## Security model

- **Author-association gate, twice.** The workflow `if:` only starts the
  job when the commenter/issue-author is `OWNER`/`MEMBER`/`COLLABORATOR`
  **and** the body contains `@jarvis`; the CLI re-checks the same gate
  from the event JSON (belt-and-suspenders — the workflow gate can drift,
  the CLI gate can't be bypassed by editing the workflow trigger list).
  Optional narrowing: set `JARVIS_GH_ALLOWLIST=login1,login2` in the
  workflow env — association must pass AND the login must be listed.
- **Fork-head refusal.** For PR comments, the CLI asks
  `gh pr view --json isCrossRepository,author` and refuses to run on a
  cross-repository (fork) head — a forker must not be able to get jarvis
  executing against code they control by having a collaborator comment.
- **Target-repo `.claude/` neutralized.** Before jarvis runs, the CLI
  renames the checkout's `.claude/` → `.claude.untrusted` so the target
  repo's hooks/settings can't hijack the agent (runner is ephemeral;
  rename is harmless).
- **No shell around the task text.** The comment body is read from
  `$GITHUB_EVENT_PATH` JSON and passed to `jarvis -p` via argv — it is
  never interpolated into a `run:` line or any shell string
  (`$(…)`/backticks in a comment stay inert). Control chars + NUL are
  stripped on top.
- **Publish creds = the runner's own `GITHUB_TOKEN`** (workflow-scoped
  `contents/pull-requests/issues: write`), auto-invalidated when the job
  ends. No long-lived write credential exists anywhere.
- **The agent itself doesn't publish.** The prompt forbids `git
  commit`/`push`/`gh`; the orchestrator does the add→diff→commit→push→PR
  sequence itself after jarvis exits.

## Knobs (workflow `env:`)

| Env | Default | Meaning |
|---|---|---|
| `JARVIS_GH_TRIGGER` | `@jarvis` | Mention that triggers a run |
| `JARVIS_GH_ALLOWLIST` | *(empty)* | Comma-separated logins; AND-narrows the association gate |
| `JARVIS_GH_TIMEOUT` | `600` | Seconds before the `jarvis -p` run is killed |
| `JARVIS_GH_DRY_RUN` | *(unset)* | `1` = log what would happen, post nothing (same as `--dry-run`) |
| `JARVIS_BIN` | `jarvis` | Path to the jarvis launcher on the runner |

## Cost

Every trigger is a full headless `jarvis -p` agent run on your provider
keys (typically one Sonnet-class session per task; multi-step tasks can
run several model-minutes) plus GitHub-hosted runner minutes (free tier:
2,000 min/mo private repos, unlimited public). The author gate is the
cost gate — only trusted associations can spend. If a task loops, the
`JARVIS_GH_TIMEOUT` (default 10 min) bounds the burn; cancel live runs
from the Actions tab.

## Troubleshooting

- **Workflow never starts** — check the `if:` gate: outside
  collaborators are `CONTRIBUTOR`/`NONE` and are filtered by design.
  Also confirm the workflow file is on the default branch.
- **`Install jarvis CLI` step fails** — `JARVIS_CLI_TOKEN` missing or
  lacks read access to `ulrichando/jarvis`.
- **Run succeeds but no PR** — read the `Run jarvis` step log:
  `no matching @jarvis event` (trigger regex didn't match — word-boundary
  match, so `@jarvisbot` doesn't count), `not authorized`, `untrusted PR
  head — refusing`, or `No changes were needed` (jarvis made no diff;
  it commented that on the thread instead).
- **`git push` rejected** — `actions/checkout` persists its token by
  default; if the target repo overrides `persist-credentials: false`,
  drop that or add `gh auth setup-git` before the run step.

## Follow-ups (deliberate v1 cuts)

- **Publish a prebuilt binary → drop `JARVIS_CLI_TOKEN`.** v1 clones +
  `bun install`s the CLI per run (~30–60 s, needs the PAT). Once the
  compiled binary from `docs/…/cli-binary-and-web-installer` work is
  published at a public URL, the install step becomes a `curl | sha256
  verify` and the PAT secret disappears.
- Reactions/progress comments on the triggering thread (ack 👀 → ✅/❌)
  — v1 only posts terminal results.
- `workflow_dispatch` manual trigger for re-runs without a fresh comment.
