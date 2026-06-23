# JARVIS evolution agency — Phase 4 design

**Date:** 2026-06-22
**Status:** design (approved forks, pending spec review)
**Parent spec:** [2026-05-24-jarvis-source-code-self-mod-design.md](2026-05-24-jarvis-source-code-self-mod-design.md)
**Related memory:** `jarvis-self-evolution-loop` (Phases 1–3 done/committed/pushed/live-proven; this is Phase 4)

## Summary

Phases 1–3 gave JARVIS a self-evolution loop: a nightly detector proposes code
changes → publishes a PR → a human approves → `jarvis-automod deploy` merges +
restarts → an external watchdog confirms health or auto-rolls-back. It runs in
**shadow/off** and is triggered **only** by the 3am nightly cron.

Phase 4 gives JARVIS **agency** over that loop — two capabilities:

- **4a. Voice-mention notification.** When a proposal lands, the next session
  JARVIS tells you about it once (the web badge already exists; this adds the
  voice channel you chose).
- **4b. On-demand, self-initiated proposals.** JARVIS can decide on his own
  judgment, at any time (not just 3am), that something is worth a code change and
  kick off a proposal **immediately** — running the spawn in an **isolated git
  worktree** so the main working tree is never disturbed.

The human **deploy-approval gate stays fully intact**: on-demand proposals only
ever produce a branch + PR + review-queue artifact. Nothing merges, restarts, or
touches the running agent without your explicit approval via `/evolution`.

## Decisions (approved 2026-06-22)

| Axis | Decision |
|---|---|
| Notification channel | Voice mention next session, **once** per proposal (seen-marker, no nagging) |
| Autonomy gate | **Propose anytime → human approves deploy** (deploy gate unchanged) |
| Concurrency model | **Isolated worktree** spawn — main tree never touched |
| Initiative | **Self-initiate** — JARVIS proposes on his own judgment, not only when asked |

### Why isolated-worktree (not the lazier clean-tree gate)

The spawn (`bin/jarvis-automod-impl`) does `git checkout master` + `git reset
--hard origin/master` + `git checkout -b` **in the main working tree**. That is
exactly what disrupted a concurrent agent on 2026-06-21. The nightly avoids this
by running at 3am behind a "skip if a turn fired in the last 900s" guard — but
when JARVIS "feels it fit" he is *by definition* mid-conversation, so that guard
would always block him.

A clean-tree guard was considered and rejected: **this box almost always has a
dirty tree** (concurrent agent sessions are the norm — 100+ uncommitted files is
typical), so "propose when the tree is clean" would degrade "anytime" to
"almost never." The isolated worktree removes the conflict entirely: the spawn
operates on its own checkout off `origin/master`, so the main tree's dirtiness is
irrelevant and concurrent work is never touched.

## Components

### 4a — Voice-mention notification

**`pipeline/automod/notify_pending.py`** (new, ~40 lines)
- `pending_proposals() -> list[Artifact]`: artifacts awaiting review, reusing the
  exact same selection `GET /api/evolution` already uses (do not re-derive the
  status set — call/share that one source of truth so the badge and the voice
  mention can never disagree).
- `new_since_notified() -> list[Artifact]`: `pending_proposals()` minus the IDs
  recorded in `~/.jarvis/auto-mods/notified.json`.
- `mark_notified(ids)`: append IDs to `notified.json` (atomic write, like the
  deploy marker).
- `session_start_block() -> str | None`: if `new_since_notified()` is non-empty,
  return a one-line context string ("You have N evolution proposal(s) awaiting
  review: «intent», «intent». Mention this once, then drop it.") **and**
  `mark_notified` them. Returns `None` when there's nothing new (inert).

**Injection point.** `pipeline/prompt_builder.py` already assembles the
supervisor prompt at session start with a frozen memory snapshot. Add the
`session_start_block()` string as a small additional frozen slot (after the
memory snapshot, before the volatile runtime-id block). Frozen = consistent with
prompt-prefix caching; mid-session proposals appear next session (same contract
as memory).

**Prompt guidance.** One short instruction in `prompts/supervisor.md`: *if the
session-start context lists new evolution proposals, surface it once, naturally,
near the start of the conversation; do not re-raise it.* This honors the
documented "don't nag / no noisy follow-up offers" rule.

**ponytail simplification.** Mark-notified happens at injection, not at the
actual utterance. If a session dies before JARVIS speaks, that proposal is marked
seen without being voiced — accepted, because the web badge is the backstop and
the proposal is never lost. (Marked with a `# ponytail:` comment at the call
site; upgrade path = mark on a confirmed assistant turn if this ever matters.)

### 4b — On-demand, self-initiated proposals

**Tool: evolve `propose_code_mod`** (`tools/code_mod.py`)
- Same `intent` + `rationale` args. Today it only enqueues; now, when
  `JARVIS_AUTOMOD_SPAWN_LIVE=1`, after enqueuing it **kicks a detached on-demand
  run** — a `setsid`-detached subprocess (`bin/jarvis-evolution-ondemand <id>`),
  **not** an in-process asyncio task. Detached so the proposal survives the turn
  ending *and* the agent's nightly recycle (proposals take minutes; an in-process
  task would be killed on restart). The voice turn returns immediately with "on
  it — I'll draft a proposal and it'll show in your review queue." When
  `SPAWN_LIVE=0` it stays enqueue-only (shadow), unchanged.
- Description loosened: JARVIS **MAY self-initiate** when he notices a recurring
  bug or real friction that genuinely needs a **code** change (not a
  memory/skill/preference save), used **SPARINGLY** and with taste. Still
  requires a concrete `intent` + a `rationale` that justifies code over the
  cheaper paths.

**Orchestrator: `pipeline/automod/ondemand.py`** (new, ~80 lines) + thin entry
`bin/jarvis-evolution-ondemand <id>` (the `setsid` target; **blocklisted** like
the other evolution bins so auto-mod can't touch its own trigger).
`run(intent_id) -> dict` — the worktree-isolated equivalent of `nightly.run()`'s
spawn+publish steps:
1. **Refuse-guards** (return a structured reason, surfaced to the user by voice):
   - active-deploy marker present → "a deploy is being verified, try again
     shortly";
   - an on-demand run already in flight (lockfile) → "already working on one".
   - (No user-active turn-guard here — that's the whole point; worktree isolation
     replaces it.)
2. `git worktree add --detach <tmp> origin/master` — fresh, inherits no dirt.
3. Run the **worktree's** `bin/jarvis-automod-impl` for `intent_id`, with the new
   tooling-root override (below) so it borrows the main repo's `.venv` + `jarvis`
   CLI. Existing blocklist + diff-gate enforcement in `finalize.py` run from the
   worktree copy and apply unchanged.
4. On success → `publish.publish(id)` pushes the branch + opens the draft PR
   (gated `JARVIS_EVOLUTION_AUTOPUBLISH`, same as nightly); artifact recorded
   `pending`. On failure → branch discarded, artifact `failed`.
5. `git worktree remove --force <tmp>` in a `finally` — always cleaned.
Returns a summary the tool turns into a spoken result ("drafted a proposal for X,
it's in your review queue" / "my proposed fix didn't pass tests").

**Tooling-root decoupling** (`bin/jarvis-automod-impl` — **blocklisted,
human-edit only; this is the one safety-surface change**)
A git worktree has no `.venv` and no convenient `bin/jarvis`. Introduce
`JARVIS_AUTOMOD_TOOLING_ROOT` (default: the impl's own `REPO_ROOT`, so existing
nightly/main-tree behavior is byte-for-byte unchanged). When set (on-demand path
sets it to the **main** repo), the impl resolves the venv python and the `jarvis`
CLI from `TOOLING_ROOT` while all **git** operations + edited files stay in its
`REPO_ROOT` (the worktree). Tests run as `TOOLING_ROOT/.venv/bin/python` with
cwd in the worktree's `src/voice-agent`, so pytest imports the **worktree's**
edited code (sys.path[0] = cwd). Purely additive; nightly never sets it.

## Data flow

```
JARVIS judgment OR user ask
  └─ propose_code_mod(intent, rationale)
       ├─ enqueue intent → queue.jsonl
       └─ (SPAWN_LIVE) background → ondemand.run(intent_id)
            ├─ refuse-guards (deploy-marker / in-flight)
            ├─ git worktree add --detach <tmp> origin/master
            ├─ worktree/bin/jarvis-automod-impl   (TOOLING_ROOT=main repo)
            │     reset→branch→jarvis edits→pytest→finalize gate→commit
            ├─ publish.publish(id) → draft PR        (AUTOPUBLISH)
            └─ worktree remove (finally)
  → artifact `pending` → /evolution badge (exists) + next-session voice mention (4a)
  → YOU approve → jarvis-automod deploy → watchdog confirm / auto-rollback (Phase 1)
```

Main working tree, concurrent agents, and the running voice-agent are untouched
through the entire on-demand path until you approve a deploy.

## Error handling

| Condition | Behavior |
|---|---|
| Worktree create fails | tool returns error; JARVIS says he couldn't start; nothing else changes |
| Spawn/tests fail | branch discarded, artifact `failed`, worktree removed; JARVIS can voice that it didn't pass |
| Blocklist / diff-gate violation | `finalize.py` rejects (unchanged); branch deleted |
| Active-deploy marker present | on-demand refused with a spoken reason |
| On-demand already in flight | refused (lock); spoken reason |
| `SPAWN_LIVE=0` (shadow) | enqueue-only, no spawn — safe default for rollout |

## Testing

- `ondemand.run` against a throwaway git-repo fixture: assert (a) a branch is
  created in the worktree, (b) the **main** fixture tree is byte-identical before
  and after, (c) the worktree is removed, (d) artifact recorded.
- Refuse-guards: deploy-marker present → refused; second concurrent call →
  refused.
- Tooling-root decoupling: impl invoked with `JARVIS_AUTOMOD_TOOLING_ROOT` set
  uses the override venv (assert via a stub).
- `notify_pending`: new-vs-already-notified set math; `session_start_block`
  returns text + marks seen; empty when nothing new; `notified.json` round-trips.
- Prompt assembly includes the block when present, omits it when `None`.
- Existing 11 watchdog + 19 evolution + broader suite stay green.

## Flags / rollout (all default OFF → ship safe, flip when ready)

- `JARVIS_AUTOMOD_ENABLED=1` — exposes `propose_code_mod` (exists).
- `JARVIS_AUTOMOD_SPAWN_LIVE=1` — makes on-demand actually spawn (else
  enqueue-only shadow) — reuses the existing spawn gate.
- `JARVIS_EVOLUTION_AUTOPUBLISH=1` — on-demand opens the PR (else branch only) —
  reuses the existing publish gate.
- `JARVIS_AUTOMOD_TOOLING_ROOT` — set by the on-demand path only; unset for
  nightly.
- Voice-mention (4a) is ungated: inert when there are no pending proposals.

**Blocklist additions** (`_state.HARD_BLOCKLIST_PATHS`): `bin/jarvis-evolution-ondemand`
and `pipeline/automod/ondemand.py` (the latter already covered by the
`pipeline/automod/` prefix). Additive only — the rule against *removing*
blocklist entries is untouched.

## Non-goals (YAGNI)

- No autonomous deploy (explicitly rejected — human gate stays).
- No email / desktop-notification channel (voice + existing badge only).
- No change to Phases 1–3 (watchdog, deploy actuator, nightly, web review) beyond
  the one additive prompt-injection slot and the additive tooling-root env var.
- No new dependency.

## Spec amendments

Append a "Phase 4 — agency" note to the parent spec
(`2026-05-24-...`) pointing here.
