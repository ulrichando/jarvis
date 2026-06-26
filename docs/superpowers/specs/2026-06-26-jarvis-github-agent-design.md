# JARVIS as the GitHub agent (Path B) — design

- **Date:** 2026-06-26
- **Status:** Approved (design); pending implementation plan
- **Owner:** Ulrich
- **Related:** PR #46 (Path A — stock `claude-code-action` bootstrap); memory `github-agents-claude-bootstrap`

## 1. Context & motivation

Path A (PR #46) put three stock `anthropics/claude-code-action` workflows on the
repo as a **bootstrap**: `@claude` interactive, auto PR review, and security
review — dormant until `ANTHROPIC_API_KEY` is set. They're Claude Code wearing a
JARVIS name tag.

The goal ("till JARVIS can do all those stuff by itself") is **Path B: JARVIS's
own CLI (`src/cli`) as the GitHub agent** — its CLAUDE.md, tools, persona, model
routing, and multi-provider proxy doing the work, on GitHub. This spec covers
**v1: an interactive `@jarvis` agent that implements a request and opens a PR.**

Feasibility is confirmed: the `jarvis` CLI runs headless (`--print` /
`--permission-mode bypassPermissions` / `--model`), reads CLAUDE.md, and (being a
full agent) already has bash/git/gh tools.

## 2. Goals / Non-goals

**Goals (v1)**
- `@jarvis <request>` in an issue or PR → JARVIS implements the change and opens a
  PR, then comments a summary.
- Run the **real** JARVIS: its CLI + the `:4000` multi-provider proxy, on a
  GitHub-hosted ephemeral runner.
- Safe on a public repo: owner-gated, same-repo-only, PR-only.

**Non-goals (v1)**
- Self-hosted / local-model execution (deferred — see §13).
- Replacing Path A's auto PR-review and security-review (they stay on the stock
  action until `@jarvis` is trusted).
- Direct pushes to `master` (never — JARVIS only opens PRs).
- Modifying `src/cli` (off-limits per CLAUDE.md; v1 uses the CLI as-is).

## 3. Decisions (resolved during brainstorming)

| Fork | Decision | Why |
|---|---|---|
| Runner | **GitHub-hosted ephemeral** (`ubuntu-latest`) | Fully isolated — zero risk to Ulrich's root-access box; safe on a public repo. |
| Provider wiring | **Run the `:4000` proxy in-runner** | The real JARVIS path — multi-provider routing + model picker; agent can run on Anthropic OR cheaper DeepSeek/Groq. |
| Agent powers | **Implement → open a PR** (owner-gated, PR-only) | The actual "JARVIS maintains its repo"; cheap because JARVIS already has the tools. |
| PR mechanics | **Workflow-driven** (JARVIS edits; workflow commits + opens PR) | Deterministic; the workflow owns branch/commit/PR shape; mirrors how `claude-code-action` works. JARVIS focuses on code. |
| CLI entry | **`scripts/run-cli.mjs`**, not `bin/jarvis`/`start.sh` | `start.sh` wraps the CLI in a `systemd-run --user` eBPF containment scope that doesn't exist on a GitHub runner. `run-cli.mjs` spawns the proxy + sets `ANTHROPIC_BASE_URL` with no systemd dependency. |

## 4. Architecture

```
issue/PR comment "@jarvis <request>"
        │
        ▼  (job gate: @jarvis mention + author_association ∈ [OWNER,MEMBER,COLLABORATOR] + same-repo)
ubuntu-latest ephemeral runner
  1. checkout (full, writable)
  2. setup Bun (+ cache ~/.bun, src/cli/node_modules)
  3. bun install      (in src/cli)
  4. assemble prompt FILE   ← issue/PR context via env, never shell-interpolated
  5. run JARVIS headless:   node src/cli/scripts/run-cli.mjs -p \
        --permission-mode bypassPermissions --model <id> < prompt.md
       └─ run-cli.mjs spawns src/proxy/server.ts (:4000), sets ANTHROPIC_BASE_URL
       └─ proxy reads provider key(s) from env (from repo secrets) → routes to provider
       └─ JARVIS edits the working tree (its own file tools)
  6. PR mechanics (workflow-driven):
       git diff --quiet && "no changes" comment, else:
       git checkout -b jarvis/issue-<n>-<run_id>
       git commit -am "<conventional msg>"   (NO Co-Authored-By trailer)
       git push origin HEAD
       gh pr create --base master ...
  7. comment JARVIS's stdout summary back on the issue/PR (GITHUB_TOKEN)
```

## 5. Components

1. **`.github/workflows/jarvis-agent.yml`** — trigger, gate, the pipeline above.
2. **`scripts/ci/jarvis-agent-run.sh`** (repo root `scripts/`, NOT `src/cli`) —
   the runner-side helper: assembles the prompt file from env, invokes
   `run-cli.mjs`, captures stdout, and runs the workflow-driven PR mechanics.
   Keeping the logic in a script (not inline YAML) makes it testable and keeps
   the workflow thin.

Both live OUTSIDE `src/cli` and use the CLI as a black box (its existing
`run-cli.mjs` + flags). No `src/cli` edits.

## 6. The JARVIS invocation (concrete)

- **Entry:** `node src/cli/scripts/run-cli.mjs` (spawns `src/proxy/server.ts`,
  sets `ANTHROPIC_BASE_URL=http://localhost:4000`).
- **Flags:** `-p` (print/headless) · `--permission-mode bypassPermissions`
  (autonomous — safe in the ephemeral, owner-gated runner) · `--model <id>`
  (configurable; default = the repo's chosen GitHub-agent model) ·
  `--output-format` text (capturable summary on stdout).
- **Prompt:** delivered on **stdin from a file** the helper assembled — never
  interpolated into a shell command.
- **Keys:** the provider key(s) matching `<model>` are exported to the job env
  from repo secrets; the spawned proxy inherits them. (e.g. Claude model →
  `ANTHROPIC_API_KEY`; DeepSeek model → `DEEPSEEK_API_KEY`.)
- **Working dir:** the checked-out repo; JARVIS reads CLAUDE.md and edits in place.

## 7. PR mechanics (workflow-driven)

After JARVIS returns, the helper:
1. `git diff --quiet` → if no changes, post a comment with JARVIS's answer (it
   was a question, not a change) and exit 0.
2. Else: create branch `jarvis/issue-<n>-<short-run-id>`, commit all changes with
   a conventional-commit message (subject from the request; **no Co-Authored-By,
   no AI attribution** per repo rule), push, `gh pr create --base master` with a
   body linking the originating issue/PR and quoting JARVIS's summary.
3. Comment back on the origin issue/PR with the PR link (or the answer).

The workflow — not JARVIS — owns branch naming, the commit, and the PR call, so
the outcome is deterministic regardless of how JARVIS phrases its turn.

## 8. Security model

- **Trigger gate (job-level `if:`):** `@jarvis` mentioned **AND**
  `author_association ∈ [OWNER,MEMBER,COLLABORATOR]` **AND** same-repo (no fork
  PRs). Identical posture to the hardened Path A `claude.yml`.
- **PR-only:** JARVIS never pushes to `master`; every change lands as a PR Ulrich
  reviews + merges. Branch protection on `master` is the backstop.
- **`bypassPermissions` is acceptable here** precisely because the runner is
  ephemeral + network-isolated-by-default + owner-gated + cannot touch `master`.
- **Injection-safety:** untrusted-ish text (issue/PR/comment bodies) is passed to
  JARVIS via a **file**, never via `run:` shell interpolation; no event field is
  placed in a shell command or `ref:`. (Matches the Path A review findings.)
- **Token scope:** `GITHUB_TOKEN` with `contents: write` (branch+commit),
  `pull-requests: write`, `issues: write`. No `id-token: write`. Provider key is
  used **only** by the in-runner proxy, never echoed.
- **Actions** SHA-pinned per repo convention.

## 9. Build / runtime

- **Bun** via `oven-sh/setup-bun` (SHA-pinned), cache `~/.bun` + `src/cli/node_modules` keyed on `src/cli/bun.lock`.
- **Timeout:** 20 min job timeout; the JARVIS step gets its own inner timeout.
- **Concurrency:** `concurrency: { group: jarvis-agent-${{ issue/PR number }}, cancel-in-progress: true }` so rapid re-mentions don't pile up.
- **Model + secret:** chosen via a workflow env/input; v1 default to a capable, cost-reasonable model routed through the proxy. Dormant until the matching provider secret exists (same green-skip gate as Path A).

## 10. Failure modes & open risks (to resolve in the plan)

1. **Proxy in CI without systemd** — `run-cli.mjs` spawns the proxy session-scoped; verify it comes up + `/health` passes on a clean runner (no systemd, no keys.env). *Mitigation:* explicit proxy health-wait + fail fast with logs.
2. **Cold `bun install` time** — first run installs `src/cli` deps; measure, cache aggressively. Risk: slow first response.
3. **JARVIS output capture** — confirm `-p --output-format text` yields a clean, postable summary on stdout (no TUI control codes). *Mitigation:* the helper strips/scopes stdout; consider `--output-format json` for a structured result.
4. **Reliability of edits** — JARVIS is an LLM agent; it may not always produce a coherent change. *Mitigation:* the no-diff path comments instead of opening an empty PR; the PR is always human-reviewed.
5. **Key availability** — Ulrich's keys were revoked (2026-06-26 leak). v1 ships dormant; it activates when a provider secret is set.

## 11. Verification plan

- **Local dry-run:** run the helper against `run-cli.mjs` with a fake issue
  payload + a real key, confirm: proxy up → JARVIS edits → branch/commit/PR path
  (against a scratch repo or `--dry-run` for the `gh` calls).
- **Live smoke:** open a test issue "@jarvis add a trivial `/healthz` doc note" →
  expect a PR from `jarvis/issue-<n>` with the change + a summary comment.
- **Gate test:** a `@jarvis` comment from a non-collaborator (or a fork PR) must
  **not** spawn the job.
- Workflow YAML lint (`actionlint`/yaml) + the existing CI on the introducing PR.

## 12. Relationship to Path A

`jarvis-agent.yml` is **additive** — it sits alongside Path A. Once `@jarvis` is
trusted, retire `claude.yml` (the `@claude` interactive). Path A's auto PR-review
and security-review remain on the stock action until a JARVIS-native review mode
is built (a natural Path B v2).

## 13. Future (post-v1)

- **Self-hosted / local-model variant** — a self-hosted runner (ideally a
  container, not the bare host) so `@jarvis` runs on Ulrich's local LLM stack:
  no cloud key, no API cost. Requires the stricter self-hosted security story.
- **JARVIS-native PR review + security review** — replace Path A's B/C with
  `jarvis -p` review prompts, retiring the stock action entirely.
- **Wire to the evolution loop** — let the existing self-evolution loop dispatch
  through this GitHub path so on-repo and local self-coding share one pipeline.

## 14. Deliverables

- `.github/workflows/jarvis-agent.yml`
- `scripts/ci/jarvis-agent-run.sh`
- (docs) this spec + a short runbook note on enabling it (set the provider secret)

**No `src/cli` modifications.** All new files live in `.github/` and root
`scripts/`, using the CLI through its existing `run-cli.mjs` entry.
