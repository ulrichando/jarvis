# CLI dynamic workflows (Fable-5 parity) + history-snip runtime — design

**Date:** 2026-07-01
**Branch:** `cli-feature-unlock`
**Status:** approved by Ulrich (2a: full parity incl. resume journal; 2b: as designed incl. nudge)

Two independent features finish the CLI feature-unlock work: the `WORKFLOW_SCRIPTS`
engine and the `HISTORY_SNIP` runtime. Both flags exist in `src/cli` with complete
integration plumbing and graceful stubs; neither is in `scripts/start.sh`. The public
donor checkout (v1.0.0) has neither. All integration call sites were mapped before
this design; the stubs were authored by prior sessions specifically so the flags could
be enabled safely.

## Ground truth

- **2a:** Claude Code shipped "dynamic workflows" publicly (Fable-5 era). The complete
  engine is embedded in the installed binary
  (`~/.npm-global/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe`, v2.1.170)
  and its full contract was extracted: `WorkflowInput`/`WorkflowOutput` from
  `sdk-tools.d.ts`, the entire model-facing tool prompt (API docs, patterns, caps,
  resume semantics), and the minified runner/loader/permission logic. This spec mirrors
  that contract. At build time, extract the tool prompt **verbatim** from the binary and
  adapt only jarvis-specific bits (drop ultracode session mode, keep opt-in gating).
- **2b:** history-snip is NOT in the public build (no SDK typings, no runtime strings;
  web search confirms nothing public). The design below is derived from jarvis's own
  wired call sites, which are the binding contract.
- **Feasibility:** the engine's `node:vm` pattern (createContext + `vm.Script` +
  `runInContext({timeout})` + async injected globals + `Promise.all`) verified working
  under Bun 1.3.12 on this box.

---

## Part 1 — Dynamic workflows (`WORKFLOW_SCRIPTS`)

### Tool contract (mirrors shipped 2.1.170 exactly unless marked *jarvis*)

Input (`WorkflowTool` zod schema):
- `script?` — self-contained JS workflow script (size-capped; upstream caps at `sC`
  bytes — use 100_000). Must begin `export const meta = { name, description, whenToUse?,
  phases? }` as a **pure literal** (no computed values), then body.
- `name?` — predefined workflow, resolved from `~/.claude/workflows/` and project
  `.claude/workflows/` (project wins by name). Files size-capped, meta-validated,
  memo-cached with explicit cache clear. No bundled built-ins (*jarvis*:
  `initBundledWorkflows` stays a no-op).
- `scriptPath?` — script file on disk; takes precedence over `script`/`name`.
- `args?` — arbitrary JSON, exposed verbatim as global `args` (JSON round-tripped into
  the VM).
- `resumeFromRunId?` — `^wf_[a-z0-9-]{6,}$`. Rejected while that run is still running
  (point at TaskStop).
- `description?`/`title?` — accepted, ignored (meta wins).
- Refinement: at least one of script|name|scriptPath.

`validateInput` (in order): resolve input → static meta parse (literal-only parser, no
execution) → **determinism guard**: reject scripts whose body matches
`Date.now()` / `Math.random()` / argless `new Date()` (breaks resume; the same
functions also **throw at runtime** inside the VM).

`call()` returns **immediately**:
`{status:'async_launched', taskId, taskType:'local_workflow', workflowName, runId,
summary, transcriptDir, scriptPath}`. Before launch: `vm.Script` syntax precheck
(failure → same output shape + `error`, task marked failed). Script auto-persisted
under the session dir (`workflows/<runId>/script.mjs`); tool-result text mirrors
upstream: scriptPath iterate hint + resume hint + "Use /workflows to watch live
progress". Completion arrives via the existing task-notification channel with result,
failures, agentCount, tokens, duration.

### Script runtime (VM)

`node:vm` context, `codeGeneration: {strings:false, wasm:false}`. Injected globals:
- `agent(prompt, opts?)` → Promise. opts: `label`, `phase`, `schema` (JSON Schema —
  forces structured output, validated with retry-on-mismatch at the tool-call layer;
  subagent's system prompt gets a StructuredOutput instruction appended), `model`,
  `isolation: 'worktree'`, `agentType` (resolved from the same registry as the Agent
  tool). Returns final text (string), validated object (schema mode), or `null`
  (user-skipped mid-run or terminal failure after retries). Subagents are told their
  final text IS the return value. Dispatched through the existing `runAgent()` with
  its own AbortController and `transcriptSubdir: workflows/<runId>` (transcripts as
  `agent-<id>.jsonl` under transcriptDir).
- `pipeline(items, ...stages)` — per-item chaining, NO barrier between stages; each
  stage receives `(prevResult, originalItem, index)`; a throwing stage drops that item
  to `null` and skips its remaining stages. Max 4096 items (explicit error).
- `parallel(thunks)` — barrier; a throwing thunk resolves to `null`; never rejects.
  Max 4096 items.
- `phase(title)` — progress grouping, titles matched exactly against `meta.phases`
  (unmatched titles get their own group).
- `log(msg)` — narrator line (capped at 1000 stored logs).
- `budget` — frozen `{total, spent(), remaining()}` from the session token budget;
  `remaining()` is Infinity when no target set.
- `args` — the input args.
- `workflow(name, args?)` — invoke a named workflow from within a script; throws on
  unknown name / child syntax error.
- `console` → routed to `log`; `setTimeout`/`clearTimeout` — abort-bound timers.
- Plain JS only (no TS syntax); async body context; NO filesystem or Node API access;
  `Date.now`/`Math.random`/argless `new Date` shadowed to throw.

**Caps (upstream values):** concurrent `agent()` calls capped at `min(16, cores-2)`
per workflow — excess queue; total agent count per run capped at 1000; 4096 items per
pipeline/parallel call.

**Runner:** loads journal → builds VM → `runInContext` with sync timeout → races the
task AbortController → settles the returned promise → JSON-serializes the result
(functions dropped) → returns `{result, agentCount, logs, failures, durationMs,
error?}` with error stacks trimmed to ≤5 frames.

**Journal / resume:** JSONL under the session dir; each completed `agent()` call is
appended as (sequence index, hash(prompt + opts), result) — **prefix semantics**, not
a bag: on `resumeFromRunId`, calls replay from the journal while the sequence matches
by position + hash; the first mismatch and everything after runs live. Same script +
args → 100% cache hit.

### Task framework integration

Extend `LocalWorkflowTaskState`: `workflowRunId`, `title?`, `phases?`
(entries `{title, detail?, model?}` — a phase-level `model` overrides agent model
resolution for that phase, per upstream), `workflowProgress: SdkWorkflowProgress[]`,
`totalTokens`, `totalToolCalls`,
`agentControllers: Map<agentId, AbortController>` (mutable Map, non-reactive — the
documented sessionHooks pattern). Progress events (`workflow_agent` entries:
`{agentId, label, phaseTitle, phaseIndex, state, tokens, toolCalls, durationMs,
error?}` + `workflow_log`) batched ~16ms → `updateTaskState` +
`emitTaskProgress({workflowProgress})`. Create `src/cli/src/types/tools.ts` exporting
`SdkWorkflowProgress` (satisfies the existing dangling type-only import) and add
optional `workflow_progress` to `SDKTaskProgressMessageSchema` (additive).
`registerTask` already emits `workflow_name` on task_started — no change.

- **kill** — task AbortController → aborts all agents (existing `markKilled` extended
  to abort controllers). Status precedence: aborted→killed, error→failed, else
  completed.
- **skip** — `skipWorkflowAgent(taskId, agentId)` aborts that agent's controller; its
  `agent()` resolves `null` with state `'skipped by user'` (upstream literal).
- **retry** — journal-based resume after the run ends (the shipped semantic; upstream
  has no live mid-run retry). `retryWorkflowAgent` stays a no-op; the detail dialog
  shows the resume snippet for failed runs instead of a retry keybinding.

### Permissions & UI

- `checkPermissions`: name-scoped allow/deny/ask rules (rule content = workflow name);
  default **ask** "Review dynamic workflow before running" with the resolved script in
  `updatedInput` so the dialog can show it; named workflows get an "always allow
  workflow <name>" suggestion. `isReadOnly()` flips to `false`.
- `WorkflowPermissionRequest.tsx` (currently null stub): renders meta name +
  description + phases + scrollable script preview, allow/deny.
- `WorkflowDetailDialog.tsx` (currently null stub): phase-grouped agent rows with
  state glyph, label, tokens, duration; `s` skip selected (running only), `x` kill,
  Esc/← back. Props contract already fixed by BackgroundTasksDialog
  (`workflow`, `onDone`, `onKill`, `onSkipAgent`, `onRetryAgent`, `onBack`).
- `/workflows` (replaces stub): lists named workflows (name, description, whenToUse,
  source) + live/recent runs with per-phase progress; non-interactive safe.
- `getWorkflowCommands(cwd)` (*jarvis* additive): each named workflow becomes a slash
  command that instructs the model to call `Workflow({name, args: $ARGUMENTS})` —
  the commands.ts merge point already exists.
- Skipped from upstream (*jarvis*): ultracode session mode, first-use token-consent
  dialog (single-user box; the permission ask covers it), remote/CCR dispatch
  (`remote_launched` never emitted), telemetry events. The opt-in gating language in
  the tool prompt is kept (explicit user request / named workflow / skill instruction).

### Files

```
src/cli/src/tools/WorkflowTool/
  WorkflowTool.ts          real tool (schema, validate, permissions, call)
  meta.ts                  static literal meta parser + determinism guard
  vmRuntime.ts             VM context assembly (globals, timers, caps, membrane)
  runWorkflow.ts           runner (journal, abort race, result serialization)
  agentCall.ts             agent() → runAgent bridge (schema mode, skip, progress)
  namedWorkflows.ts        loader (~/.claude/workflows + project, cache)
  journal.ts               (prompt,opts)-keyed JSONL result cache
  prompt.ts                verbatim upstream tool prompt (adapted)
  WorkflowPermissionRequest.tsx
  bundled/index.ts         stays no-op
src/cli/src/tasks/LocalWorkflowTask/LocalWorkflowTask.ts   extended state + skip/kill
src/cli/src/components/tasks/WorkflowDetailDialog.tsx      real UI
src/cli/src/commands/workflows/workflows.ts                real listing
src/cli/src/tools/WorkflowTool/createWorkflowCommand.ts    real slash commands
src/cli/src/types/tools.ts                                 new (SdkWorkflowProgress)
src/cli/src/entrypoints/sdk/coreSchemas.ts                 additive workflow_progress
```

---

## Part 2 — History-snip runtime (`HISTORY_SNIP`)

No public reference exists; the binding contracts are jarvis's own call sites
(query.ts:401, QueryEngine.ts snipReplay, attachments.ts nudge, messages.ts [id:]
tags + `SNIP_NUDGE_TEXT` require + `projectSnippedView` filter, sessionStorage.ts
`applySnipRemovals` — which is LIVE code already reading
`snipMetadata.removedUuids` on resume).

### Semantics

- User messages already get `[id:xxxxxx]` anchors (`deriveShortMessageId`: 6-char
  base36 of the uuid) when the runtime is enabled.
- **SnipTool** input changes from the placeholder `start_line`/`end_line` to
  `start_id`/`end_id` (strings — the anchors the model can see). Range = start
  anchor's message through the end anchor's **segment** (everything up to, not
  including, the next non-meta user message). Tool `call()` does best-effort checks
  and returns a confirmation; the **authoritative** validation and the actual
  mutation happen at the next query boundary via `snipCompactIfNeeded` (tools never
  mutate the store mid-turn; an invalid range surfaces as a no-op boundary-less pass
  plus a meta notice).
- **Validation rails:** both anchors resolve; start ≤ end in order; range excludes
  the current turn and the latest non-meta user message; range cannot orphan a
  tool_use/tool_result pair (expand to pair boundaries or reject); no overlap with an
  existing snip; system messages and boundaries never removed.
- **Boundary message:** one system message, subtype `snip_boundary`, carrying
  `snipMetadata: {removedUuids, tokensFreed, label}` — exactly the shape
  `applySnipRemovals` already replays on session load. Snipped-ness is derived
  statelessly: a message is snipped iff its uuid appears in a boundary's
  `removedUuids` within the same list.
- **`snipCompactIfNeeded(messages, {force?})`:** normal pass = project existing
  boundaries out of the outgoing list + execute any pending un-applied Snip tool-use
  (validate → build boundary → insert at range position) → `{messages, tokensFreed,
  executed, boundaryMessage?}`. `tokensFreed` feeds autocompact (already plumbed).
  Force pass (QueryEngine snipReplay, headless) = physically apply all boundary
  removals to the store to bound SDK memory.
- **`projectSnippedView(messages)`** filters snipped messages (keeps boundaries) —
  model-facing paths only; REPL scrollback keeps everything.
  `projectSnipMessages` = alias of the same projection (no distinct caller found).
- **`isSnipBoundaryMessage(m)`** — subtype check.
- **`shouldNudgeForSnips(messages)`** — ≥10k estimated tokens of growth since the
  last nudge / snip marker / snip boundary / compact boundary (matches the
  attachments.ts doc comment).
- **`SNIP_NUDGE_TEXT`** — new export (messages.ts already requires it): tells the
  model to consider Snip with `[id:]` anchors for concluded explorations/superseded
  output; never snip content still needed.
- **`isSnipRuntimeEnabled()`** — `true` unless `JARVIS_HISTORY_SNIP=0` (env
  kill-switch; the compile-time flag is the real gate).
- **`SnipBoundaryMessage.tsx`** — dim one-liner: `✂ N messages snipped (~X tokens)`.

Files: `services/compact/snipCompact.ts`, `services/compact/snipProjection.ts`,
`tools/SnipTool/SnipTool.ts` (schema + call), `components/messages/SnipBoundaryMessage.tsx`.

---

## Rollout & verification

1. Build + unit-test each part with modules imported directly (`feature()` is false
   under `bun test` — by design; runtime modules must be testable standalone).
2. Add `--feature=WORKFLOW_SCRIPTS` and `--feature=HISTORY_SNIP` to
   `src/cli/scripts/start.sh` **last**, after everything parses — a flag whose module
   is broken can hang the boot.
3. Verify: `bun build <file> --no-bundle` per changed file + targeted bundle;
   `bun test` (existing 201 must stay green); `bin/jarvis -p "say OK"` from repo
   root; then an end-to-end workflow smoke (`bin/jarvis -p` asking for a trivial
   2-agent workflow) and a snip smoke in a REPL session.

Tests (bun): meta parser (literals, rejects computed/TS syntax, determinism guard);
pipeline/parallel semantics (no-barrier interleave, null-on-throw, item caps);
journal round-trip + resume prefix logic; agent() skip/null paths; workflow loader
precedence; snip: anchor resolution, segment-range math, tool-pair integrity,
protected-tail rejection, boundary create/project round-trip, removedUuids shape
matches applySnipRemovals, nudge pacing reset points.

## Out of scope

Remote/CCR workflow dispatch; ultracode session mode; usage-consent dialog; bundled
built-in workflows; live mid-run retry; auto-snip policy (model-directed only);
upstream telemetry. The voice-agent, desktop, web, and android trees are untouched.
