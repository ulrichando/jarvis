# CLI Dynamic Workflows + History-Snip Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the two remaining stubbed CLI capabilities into real engines — `WORKFLOW_SCRIPTS` (Fable-5 dynamic-workflows parity: JS orchestration scripts running dozens of subagents in a vm sandbox) and `HISTORY_SNIP` (id-anchored conversation-range removal) — then enable both flags.

**Architecture:** Both features already have complete integration plumbing in `src/cli` behind `feature('X')` macros with graceful stubs. We replace the stubs with real implementations, verifying each module standalone (`feature()` is false under `bun test`, so runtime modules are imported directly). The flags go into `scripts/start.sh` **last**, after every module parses, because a broken gated `require()` can hang the boot.

**Tech Stack:** TypeScript, Bun 1.3.12, `node:vm`, zod/v4, Ajv (already vendored for StructuredOutput), React/Ink for dialogs.

**Reference contract:** `docs/superpowers/specs/2026-07-01-cli-dynamic-workflows-and-history-snip-design.md`. The upstream tool prompt + schemas were extracted from the installed binary at `~/.npm-global/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe` (v2.1.170) — re-extract verbatim strings from there when a task says "verbatim".

**Verification rules (from `.claude/rules/cli.md` + memory `cli-compiled-verify-with-bun`):**
- Parse check: `cd src/cli && bun build <file> --no-bundle`
- Import-path check: `cd src/cli && bun build <changed-file> --no-bundle` (bundling a single changed file catches wrong import paths)
- NEVER whole-graph-bundle `cli.tsx` as a build check — it always fails on lazy/native imports.
- `import type { … } from '../../types/message.js'` is a **type-only** import (the module has no runtime file; types are erased at build). Match the existing stub pattern; don't try to create that module.
- Tests: `cd src/cli && bun test <path>` for a file, `bun test` for the suite (201 existing must stay green).
- Boot check after ANY start.sh flag: `bin/jarvis -p "say OK"` from repo root must return.

**Commit discipline:** explicit pathspec only (`git add <paths> && git commit -- <paths>`), NEVER `git add -A` (repo carries 100+ files from parallel sessions). No Co-Authored-By / attribution trailers.

---

## File Structure

**Part 1 — Workflows (`src/cli/src/`)**
- `tools/WorkflowTool/meta.ts` — parse+validate the `export const meta` literal; determinism guard. *(new)*
- `tools/WorkflowTool/journal.ts` — sequence-indexed (prompt,opts)→result JSONL cache. *(new)*
- `tools/WorkflowTool/agentCall.ts` — `agent()` → `runAgent()` bridge (schema mode, skip, progress). *(new)*
- `tools/WorkflowTool/vmRuntime.ts` — vm context assembly: agent/parallel/pipeline/phase/log/budget/args/workflow, caps, timers. *(new)*
- `tools/WorkflowTool/runWorkflow.ts` — runner: build context, run script, abort race, serialize result. *(new)*
- `tools/WorkflowTool/namedWorkflows.ts` — load `~/.claude/workflows` + project `.claude/workflows`. *(new)*
- `tools/WorkflowTool/prompt.ts` — verbatim upstream tool prompt (replaces 1-line stub).
- `tools/WorkflowTool/WorkflowTool.ts` — real tool (replaces graceful stub).
- `tools/WorkflowTool/createWorkflowCommand.ts` — named workflows → slash commands (replaces `[]` stub).
- `tools/WorkflowTool/WorkflowPermissionRequest.tsx` — real permission dialog (replaces null stub).
- `tasks/LocalWorkflowTask/LocalWorkflowTask.ts` — extend state + skip/kill (replaces no-op skip/retry).
- `components/tasks/WorkflowDetailDialog.tsx` — real detail UI (replaces null stub).
- `commands/workflows/workflows.ts` — real `/workflows` listing (replaces stub).
- `types/tools.ts` — `SdkWorkflowProgress` type (satisfies existing dangling import). *(new)*
- `entrypoints/sdk/coreSchemas.ts` — additive `workflow_progress` on progress schema.

**Part 2 — Snip (`src/cli/src/`)**
- `services/compact/snipCompact.ts` — runtime enable, nudge pacing, `snipCompactIfNeeded`, `SNIP_NUDGE_TEXT`.
- `services/compact/snipProjection.ts` — boundary detection + projection.
- `tools/SnipTool/SnipTool.ts` — `start_id`/`end_id` schema + call (replaces `start_line`/`end_line`).
- `components/messages/SnipBoundaryMessage.tsx` — boundary render (replaces null stub).

**Enable last**
- `scripts/start.sh` — add `--feature=WORKFLOW_SCRIPTS` and `--feature=HISTORY_SNIP`.

---

# PART 1 — DYNAMIC WORKFLOWS

### Task 1: Workflow meta parser + determinism guard

**Files:**
- Create: `src/cli/src/tools/WorkflowTool/meta.ts`
- Test: `src/cli/src/tools/WorkflowTool/meta.test.ts`

The `meta` block must be a pure object literal (no variables/calls/spreads/templates). We parse it statically WITHOUT executing the script (security + it must work before the vm runs). Approach: locate `export const meta =` , brace-match the object literal, `JSON5`-free parse via `Function`-in-a-frozen-scope is unsafe — instead use `vm.runInNewContext` on JUST the literal with all globals denied, which evaluates a pure literal but throws on any identifier reference. The determinism guard is a regex over the script body.

- [ ] **Step 1: Write the failing test**

```typescript
// src/cli/src/tools/WorkflowTool/meta.test.ts
import { expect, test } from 'bun:test'
import { parseWorkflowMeta, checkDeterminism } from './meta.js'

test('parses a pure literal meta', () => {
  const src = `export const meta = { name: 'find-flaky', description: 'x', phases: [{ title: 'Scan' }] }
phase('Scan')`
  const r = parseWorkflowMeta(src)
  expect('error' in r).toBe(false)
  if ('error' in r) return
  expect(r.meta.name).toBe('find-flaky')
  expect(r.meta.phases?.[0]?.title).toBe('Scan')
  expect(r.scriptBody.startsWith("phase('Scan')")).toBe(true)
})

test('rejects missing meta', () => {
  const r = parseWorkflowMeta(`phase('x')`)
  expect('error' in r).toBe(true)
})

test('rejects computed meta (variable reference)', () => {
  const r = parseWorkflowMeta(`const n = 'x'\nexport const meta = { name: n, description: 'd' }`)
  expect('error' in r).toBe(true)
})

test('rejects meta without required name/description', () => {
  const r = parseWorkflowMeta(`export const meta = { name: 'x' }`)
  expect('error' in r).toBe(true)
})

test('determinism guard flags Date.now / Math.random / new Date()', () => {
  expect(checkDeterminism('const t = Date.now()')).toBe(false)
  expect(checkDeterminism('Math.random()')).toBe(false)
  expect(checkDeterminism('new Date()')).toBe(false)
  expect(checkDeterminism('new Date(args.ts)')).toBe(true)
  expect(checkDeterminism('await agent("x")')).toBe(true)
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cli && bun test src/tools/WorkflowTool/meta.test.ts`
Expected: FAIL — `Cannot find module './meta.js'`.

- [ ] **Step 3: Write the implementation**

```typescript
// src/cli/src/tools/WorkflowTool/meta.ts
import vm from 'node:vm'

export type WorkflowPhase = { title: string; detail?: string; model?: string }
export type WorkflowMeta = {
  name: string
  description: string
  whenToUse?: string
  phases?: WorkflowPhase[]
}
export type ParsedWorkflow = { meta: WorkflowMeta; scriptBody: string }

// Match determinism guard to upstream: reject the three non-deterministic
// primitives that would break journal resume. argless `new Date()` only.
const NON_DETERMINISTIC =
  /\bDate\s*\.\s*now\b|\bMath\s*\.\s*random\b|\bnew\s+Date\s*\(\s*\)/

export function checkDeterminism(scriptBody: string): boolean {
  return !NON_DETERMINISTIC.test(scriptBody)
}

// Find `export const meta =` and brace-match the following object literal.
// Returns the literal text and the remaining script body.
function sliceMetaLiteral(
  src: string,
): { literal: string; body: string } | { error: string } {
  const m = /export\s+const\s+meta\s*=\s*/.exec(src)
  if (!m) return { error: "Workflow script must begin with `export const meta = {...}`" }
  let i = m.index + m[0].length
  if (src[i] !== '{') return { error: '`meta` must be an object literal' }
  let depth = 0
  let inStr: string | null = null
  let start = i
  for (; i < src.length; i++) {
    const c = src[i]
    if (inStr) {
      if (c === '\\') { i++; continue }
      if (c === inStr) inStr = null
      continue
    }
    if (c === '"' || c === "'" || c === '`') { inStr = c; continue }
    if (c === '{') depth++
    else if (c === '}') {
      depth--
      if (depth === 0) {
        const literal = src.slice(start, i + 1)
        const body = src.slice(i + 1).replace(/^\s*;?\s*/, '')
        return { literal, body }
      }
    }
  }
  return { error: 'Unterminated `meta` object literal' }
}

export function parseWorkflowMeta(
  src: string,
): ParsedWorkflow | { error: string } {
  const sliced = sliceMetaLiteral(src)
  if ('error' in sliced) return sliced

  // Evaluate ONLY the literal in a globals-denied context. A pure literal
  // evaluates; any identifier / call / spread throws ReferenceError.
  // Evaluate the literal in an empty (null-proto) context with codegen denied
  // and a hard timeout. A pure data literal evaluates; any identifier ref,
  // call, or spread throws (no globals) → we reject as "not a pure literal".
  // Denying codeGeneration blocks eval/new-Function reach-arounds during parse.
  // (Hardening option for the executor: if a JS parser like `acorn` is already
  // vendored, prefer walking the AST and evaluating ONLY literal node types —
  // strictly safer than any evaluator. Baseline below matches upstream `K0`.)
  let meta: unknown
  try {
    meta = vm.runInNewContext(`(${sliced.literal})`, Object.create(null), {
      timeout: 100,
      contextCodeGeneration: { strings: false, wasm: false },
    })
  } catch {
    return {
      error:
        '`meta` must be a pure object literal (no variables, calls, spreads, or template interpolation)',
    }
  }
  if (typeof meta !== 'object' || meta === null) {
    return { error: '`meta` must be an object literal' }
  }
  const mm = meta as Record<string, unknown>
  if (typeof mm.name !== 'string' || !mm.name) {
    return { error: '`meta.name` is required' }
  }
  if (typeof mm.description !== 'string' || !mm.description) {
    return { error: '`meta.description` is required' }
  }
  if (mm.phases !== undefined && !Array.isArray(mm.phases)) {
    return { error: '`meta.phases` must be an array' }
  }
  return {
    meta: {
      name: mm.name,
      description: mm.description,
      whenToUse: typeof mm.whenToUse === 'string' ? mm.whenToUse : undefined,
      phases: mm.phases as WorkflowPhase[] | undefined,
    },
    scriptBody: sliced.body,
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/cli && bun test src/tools/WorkflowTool/meta.test.ts`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/cli/src/tools/WorkflowTool/meta.ts src/cli/src/tools/WorkflowTool/meta.test.ts
git commit -m "feat(cli): workflow meta parser + determinism guard" -- src/cli/src/tools/WorkflowTool/meta.ts src/cli/src/tools/WorkflowTool/meta.test.ts
```

---

### Task 2: Progress types (`SdkWorkflowProgress`)

**Files:**
- Create: `src/cli/src/types/tools.ts`
- Modify: `src/cli/src/entrypoints/sdk/coreSchemas.ts` (additive `workflow_progress` on `SDKTaskProgressMessageSchema`)

`utils/task/sdkProgress.ts` already imports `SdkWorkflowProgress` from `'../../types/tools.js'` (a dangling type-only import today). Define it.

- [ ] **Step 1: Create the type module**

```typescript
// src/cli/src/types/tools.ts

// One progress row emitted per workflow agent lifecycle event, plus a
// narrator-log variant. Batched by WorkflowTool.call and surfaced via
// emitTaskProgress({workflowProgress}) + the WorkflowDetailDialog.
export type SdkWorkflowAgentProgress = {
  type: 'workflow_agent'
  agentId: string
  label: string
  phase?: string
  phaseTitle?: string
  phaseIndex?: number
  state: 'running' | 'done' | 'error'
  tokens?: number
  toolCalls?: number
  durationMs?: number
  error?: string
}

export type SdkWorkflowLog = {
  type: 'workflow_log'
  message: string
}

export type SdkWorkflowProgress = SdkWorkflowAgentProgress | SdkWorkflowLog
```

- [ ] **Step 2: Parse check**

Run: `cd src/cli && bun build src/types/tools.ts --no-bundle && bun build src/utils/task/sdkProgress.ts --no-bundle`
Expected: both print compiled JS (no resolve error).

- [ ] **Step 3: Add additive schema field**

In `src/cli/src/entrypoints/sdk/coreSchemas.ts`, find `SDKTaskProgressMessageSchema` (search `subtype: z.literal('task_progress')`). Add after the `summary: z.string().optional(),` line, before `uuid:`:

```typescript
    workflow_progress: z.array(z.any()).optional(),
```

- [ ] **Step 4: Parse check**

Run: `cd src/cli && bun build src/entrypoints/sdk/coreSchemas.ts --no-bundle`
Expected: compiles.

- [ ] **Step 5: Commit**

```bash
git add src/cli/src/types/tools.ts src/cli/src/entrypoints/sdk/coreSchemas.ts
git commit -m "feat(cli): SdkWorkflowProgress type + additive workflow_progress schema field" -- src/cli/src/types/tools.ts src/cli/src/entrypoints/sdk/coreSchemas.ts
```

---

### Task 3: Journal (prefix-semantics result cache)

**Files:**
- Create: `src/cli/src/tools/WorkflowTool/journal.ts`
- Test: `src/cli/src/tools/WorkflowTool/journal.test.ts`

Resume replays completed `agent()` results while the call **sequence** matches by position + hash(prompt,opts). First mismatch → live from there. In-memory during a run; persisted JSONL under the session dir so a killed run can resume.

- [ ] **Step 1: Write the failing test**

```typescript
// src/cli/src/tools/WorkflowTool/journal.test.ts
import { expect, test } from 'bun:test'
import { WorkflowJournal, hashCall } from './journal.js'

test('hashCall is stable across equal (prompt,opts)', () => {
  expect(hashCall('p', { schema: { a: 1 } })).toBe(hashCall('p', { schema: { a: 1 } }))
  expect(hashCall('p', {})).not.toBe(hashCall('q', {}))
})

test('prefix replay: matching prefix returns cached, first mismatch goes live', () => {
  const prior = new WorkflowJournal()
  prior.record('p1', {}, 'r1')
  prior.record('p2', {}, 'r2')
  prior.record('p3', {}, 'r3')

  const resume = WorkflowJournal.fromEntries(prior.entries())
  // call 0 matches -> cached
  expect(resume.lookup(0, 'p1', {})).toEqual({ hit: true, result: 'r1' })
  // call 1 diverges -> miss, and it POISONS the rest
  expect(resume.lookup(1, 'CHANGED', {})).toEqual({ hit: false })
  // call 2 must NOT return cache even though p3 matches by hash
  expect(resume.lookup(2, 'p3', {})).toEqual({ hit: false })
})

test('lookup miss when index beyond journal', () => {
  const j = new WorkflowJournal()
  expect(j.lookup(0, 'p', {})).toEqual({ hit: false })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cli && bun test src/tools/WorkflowTool/journal.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Write the implementation**

```typescript
// src/cli/src/tools/WorkflowTool/journal.ts
import { createHash } from 'node:crypto'

export type JournalEntry = { hash: string; result: unknown }

// Stable structural hash of a workflow agent() call. JSON.stringify with
// sorted keys so opts key-order can't change the hash.
export function hashCall(prompt: string, opts: unknown): string {
  const stable = JSON.stringify({ prompt, opts }, (_k, v) =>
    v && typeof v === 'object' && !Array.isArray(v)
      ? Object.fromEntries(Object.entries(v).sort(([a], [b]) => a.localeCompare(b)))
      : v,
  )
  return createHash('sha256').update(stable).digest('hex').slice(0, 16)
}

export class WorkflowJournal {
  private recorded: JournalEntry[] = []
  private prior: JournalEntry[] = []
  // Once a resume call diverges, every later call runs live even if its hash
  // coincidentally matches a prior entry. This is the prefix invariant.
  private diverged = false

  static fromEntries(entries: JournalEntry[]): WorkflowJournal {
    const j = new WorkflowJournal()
    j.prior = entries
    return j
  }

  entries(): JournalEntry[] {
    return this.recorded
  }

  lookup(
    index: number,
    prompt: string,
    opts: unknown,
  ): { hit: true; result: unknown } | { hit: false } {
    if (this.diverged) return { hit: false }
    const prior = this.prior[index]
    if (!prior) return { hit: false }
    if (prior.hash !== hashCall(prompt, opts)) {
      this.diverged = true
      return { hit: false }
    }
    return { hit: true, result: prior.result }
  }

  record(prompt: string, opts: unknown, result: unknown): void {
    this.recorded.push({ hash: hashCall(prompt, opts), result })
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/cli && bun test src/tools/WorkflowTool/journal.test.ts`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/cli/src/tools/WorkflowTool/journal.ts src/cli/src/tools/WorkflowTool/journal.test.ts
git commit -m "feat(cli): workflow journal with prefix-semantics resume cache" -- src/cli/src/tools/WorkflowTool/journal.ts src/cli/src/tools/WorkflowTool/journal.test.ts
```

---

### Task 4: `pipeline()` / `parallel()` combinators

**Files:**
- Create: `src/cli/src/tools/WorkflowTool/combinators.ts`
- Test: `src/cli/src/tools/WorkflowTool/combinators.test.ts`

Pure control-flow — no vm, no agents (agent is injected). Extracted so the tricky no-barrier / null-on-throw / item-cap semantics are unit-tested in isolation.

- [ ] **Step 1: Write the failing test**

```typescript
// src/cli/src/tools/WorkflowTool/combinators.test.ts
import { expect, test } from 'bun:test'
import { runParallel, runPipeline, MAX_ITEMS } from './combinators.js'

test('parallel awaits all, null on throw, never rejects', async () => {
  const r = await runParallel([
    async () => 1,
    async () => { throw new Error('boom') },
    async () => 3,
  ])
  expect(r).toEqual([1, null, 3])
})

test('pipeline chains stages per item, passes (prev, item, index)', async () => {
  const seen: Array<[unknown, unknown, number]> = []
  const r = await runPipeline(
    ['a', 'b'],
    async (item: string) => item.toUpperCase(),
    async (prev: string, item: string, i: number) => {
      seen.push([prev, item, i])
      return `${prev}-${item}-${i}`
    },
  )
  expect(r).toEqual(['A-a-0', 'B-b-1'])
  expect(seen).toContainEqual(['A', 'a', 0])
})

test('pipeline drops a throwing item to null and skips its later stages', async () => {
  let stage2Calls = 0
  const r = await runPipeline(
    ['ok', 'bad'],
    async (item: string) => { if (item === 'bad') throw new Error('x'); return item },
    async (prev: string) => { stage2Calls++; return prev + '!' },
  )
  expect(r).toEqual(['ok!', null])
  expect(stage2Calls).toBe(1)
})

test('item cap is an explicit error', async () => {
  const big = Array.from({ length: MAX_ITEMS + 1 }, (_, i) => i)
  await expect(runParallel(big.map(() => async () => 1))).rejects.toThrow(/at most/)
  await expect(runPipeline(big, async (x: number) => x)).rejects.toThrow(/at most/)
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cli && bun test src/tools/WorkflowTool/combinators.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Write the implementation**

```typescript
// src/cli/src/tools/WorkflowTool/combinators.ts

export const MAX_ITEMS = 4096

function assertItemCap(n: number): void {
  if (n > MAX_ITEMS) {
    throw new Error(
      `A single parallel()/pipeline() call accepts at most ${MAX_ITEMS} items (got ${n}).`,
    )
  }
}

// Barrier: await all thunks; a throwing thunk resolves to null; never rejects.
export async function runParallel<T>(
  thunks: Array<() => Promise<T>>,
): Promise<Array<T | null>> {
  assertItemCap(thunks.length)
  return Promise.all(
    thunks.map(async t => {
      try {
        return await t()
      } catch {
        return null
      }
    }),
  )
}

// No-barrier pipeline: each item flows through all stages independently.
// Stage callback receives (prevResult, originalItem, index). A stage that
// throws drops that item to null and skips its remaining stages.
export async function runPipeline(
  items: unknown[],
  ...stages: Array<(prev: unknown, item: unknown, index: number) => Promise<unknown>>
): Promise<Array<unknown | null>> {
  assertItemCap(items.length)
  return Promise.all(
    items.map(async (item, index) => {
      let prev: unknown = item
      for (const stage of stages) {
        try {
          prev = await stage(prev, item, index)
        } catch {
          return null
        }
      }
      return prev
    }),
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/cli && bun test src/tools/WorkflowTool/combinators.test.ts`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/cli/src/tools/WorkflowTool/combinators.ts src/cli/src/tools/WorkflowTool/combinators.test.ts
git commit -m "feat(cli): workflow pipeline/parallel combinators" -- src/cli/src/tools/WorkflowTool/combinators.ts src/cli/src/tools/WorkflowTool/combinators.test.ts
```

---

### Task 5: Concurrency limiter

**Files:**
- Create: `src/cli/src/tools/WorkflowTool/limiter.ts`
- Test: `src/cli/src/tools/WorkflowTool/limiter.test.ts`

Concurrent `agent()` calls cap at `min(16, cores-2)`; total per run caps at 1000. A tiny semaphore wraps every `agent()` invocation.

- [ ] **Step 1: Write the failing test**

```typescript
// src/cli/src/tools/WorkflowTool/limiter.test.ts
import { expect, test } from 'bun:test'
import { ConcurrencyLimiter, computeConcurrency, TOTAL_AGENT_CAP } from './limiter.js'

test('computeConcurrency = min(16, cores-2), floor 1', () => {
  expect(computeConcurrency(4)).toBe(2)
  expect(computeConcurrency(64)).toBe(16)
  expect(computeConcurrency(1)).toBe(1)
})

test('never runs more than `max` at once', async () => {
  const lim = new ConcurrencyLimiter(2)
  let active = 0
  let peak = 0
  const task = () => lim.run(async () => {
    active++; peak = Math.max(peak, active)
    await new Promise(r => setTimeout(r, 10))
    active--
  })
  await Promise.all([task(), task(), task(), task(), task()])
  expect(peak).toBeLessThanOrEqual(2)
})

test('total cap throws past TOTAL_AGENT_CAP', async () => {
  const lim = new ConcurrencyLimiter(4)
  lim._forceCount(TOTAL_AGENT_CAP)
  await expect(lim.run(async () => 1)).rejects.toThrow(/agent cap|1000/)
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cli && bun test src/tools/WorkflowTool/limiter.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Write the implementation**

```typescript
// src/cli/src/tools/WorkflowTool/limiter.ts
import { cpus } from 'node:os'

export const TOTAL_AGENT_CAP = 1000

export function computeConcurrency(cores = cpus().length): number {
  return Math.max(1, Math.min(16, cores - 2))
}

// Minimal FIFO semaphore + lifetime counter. Guards both the concurrent
// slot count and the 1000-agent runaway backstop.
export class ConcurrencyLimiter {
  private active = 0
  private total = 0
  private queue: Array<() => void> = []
  constructor(private readonly max: number) {}

  _forceCount(n: number): void {
    this.total = n
  }

  async run<T>(fn: () => Promise<T>): Promise<T> {
    if (this.total >= TOTAL_AGENT_CAP) {
      throw new Error(
        `Workflow exceeded the ${TOTAL_AGENT_CAP}-agent lifetime cap (runaway loop backstop).`,
      )
    }
    this.total++
    if (this.active >= this.max) {
      await new Promise<void>(resolve => this.queue.push(resolve))
    }
    this.active++
    try {
      return await fn()
    } finally {
      this.active--
      const next = this.queue.shift()
      if (next) next()
    }
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/cli && bun test src/tools/WorkflowTool/limiter.test.ts`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/cli/src/tools/WorkflowTool/limiter.ts src/cli/src/tools/WorkflowTool/limiter.test.ts
git commit -m "feat(cli): workflow concurrency limiter (min(16,cores-2), 1000 cap)" -- src/cli/src/tools/WorkflowTool/limiter.ts src/cli/src/tools/WorkflowTool/limiter.test.ts
```

---

### Task 6: `agent()` bridge to `runAgent`

**Files:**
- Create: `src/cli/src/tools/WorkflowTool/agentCall.ts`
- Test: `src/cli/src/tools/WorkflowTool/agentCall.test.ts`

Bridges the vm's `agent(prompt, opts)` to jarvis's `runAgent()`. Because `runAgent` needs a full `ToolUseContext` (not available under `bun test`), the module takes an **injected dispatcher** so it's unit-testable; the real dispatcher is wired in Task 8 (`vmRuntime`). This keeps the schema-mode / skip-null / journal / progress logic testable in isolation.

- [ ] **Step 1: Write the failing test**

```typescript
// src/cli/src/tools/WorkflowTool/agentCall.test.ts
import { expect, test } from 'bun:test'
import { makeAgentFn } from './agentCall.js'
import { WorkflowJournal } from './journal.js'
import { ConcurrencyLimiter } from './limiter.js'

function harness(dispatch: any) {
  const progress: any[] = []
  const journal = new WorkflowJournal()
  const agent = makeAgentFn({
    dispatch,
    journal,
    limiter: new ConcurrencyLimiter(4),
    onProgress: p => progress.push(p),
    getPhase: () => 'Scan',
    nextIndex: (() => { let i = 0; return () => i++ })(),
    signal: new AbortController().signal,
  })
  return { agent, progress, journal }
}

test('returns text result and emits done progress', async () => {
  const { agent, progress } = harness(async () => ({ text: 'hello', tokens: 5, toolCalls: 1 }))
  const r = await agent('do a thing', { label: 'l1' })
  expect(r).toBe('hello')
  expect(progress.some(p => p.state === 'running')).toBe(true)
  expect(progress.some(p => p.state === 'done')).toBe(true)
})

test('schema mode returns the structured object', async () => {
  const { agent } = harness(async () => ({ structured: { ok: true }, tokens: 1, toolCalls: 0 }))
  const r = await agent('x', { schema: { type: 'object' } })
  expect(r).toEqual({ ok: true })
})

test('skip resolves null with skipped-by-user state', async () => {
  const { agent, progress } = harness(async () => ({ skipped: true }))
  const r = await agent('x', {})
  expect(r).toBeNull()
  expect(progress.find(p => p.state === 'error')?.error).toBe('skipped by user')
})

test('terminal failure resolves null with error progress', async () => {
  const { agent, progress } = harness(async () => { throw new Error('api dead') })
  const r = await agent('x', {})
  expect(r).toBeNull()
  expect(progress.find(p => p.state === 'error')?.error).toContain('api dead')
})

test('journal hit short-circuits dispatch', async () => {
  let dispatched = 0
  const { agent, journal } = harness(async () => { dispatched++; return { text: 'live', tokens: 0, toolCalls: 0 } })
  await agent('p', {})            // records
  const resumed = WorkflowJournal.fromEntries(journal.entries())
  const agent2 = makeAgentFn({
    dispatch: async () => { dispatched++; return { text: 'SHOULD-NOT-RUN', tokens: 0, toolCalls: 0 } },
    journal: resumed, limiter: new ConcurrencyLimiter(4),
    onProgress: () => {}, getPhase: () => undefined,
    nextIndex: (() => { let i = 0; return () => i++ })(),
    signal: new AbortController().signal,
  })
  const r = await agent2('p', {})
  expect(r).toBe('live')
  expect(dispatched).toBe(1)      // only the first, live call
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cli && bun test src/tools/WorkflowTool/agentCall.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Write the implementation**

```typescript
// src/cli/src/tools/WorkflowTool/agentCall.ts
import type { SdkWorkflowProgress } from '../../types/tools.js'
import type { WorkflowJournal } from './journal.js'
import type { ConcurrencyLimiter } from './limiter.js'

export type AgentOpts = {
  label?: string
  phase?: string
  schema?: Record<string, unknown>
  model?: string
  isolation?: 'worktree'
  agentType?: string
}

// The real dispatcher (Task 8) runs runAgent and reduces its message stream.
// Under test it's a stub. Returns exactly one of: text | structured | skipped.
export type DispatchResult =
  | { text: string; tokens: number; toolCalls: number; agentId?: string }
  | { structured: unknown; tokens: number; toolCalls: number; agentId?: string }
  | { skipped: true }

export type Dispatch = (
  prompt: string,
  opts: AgentOpts,
  signal: AbortSignal,
) => Promise<DispatchResult>

export type AgentFnDeps = {
  dispatch: Dispatch
  journal: WorkflowJournal
  limiter: ConcurrencyLimiter
  onProgress: (p: SdkWorkflowProgress) => void
  getPhase: () => string | undefined
  nextIndex: () => number
  signal: AbortSignal
}

export function makeAgentFn(deps: AgentFnDeps) {
  return async function agent(
    prompt: string,
    opts: AgentOpts = {},
  ): Promise<unknown> {
    const index = deps.nextIndex()

    const cached = deps.journal.lookup(index, prompt, opts)
    if (cached.hit) return cached.result

    const phaseTitle = opts.phase ?? deps.getPhase()
    const label = opts.label ?? prompt.slice(0, 60)
    const agentIdRef = `wfa_${index}`

    deps.onProgress({
      type: 'workflow_agent',
      agentId: agentIdRef,
      label,
      phase: phaseTitle,
      phaseTitle,
      state: 'running',
    })

    try {
      const result = await deps.limiter.run(() =>
        deps.dispatch(prompt, opts, deps.signal),
      )

      if ('skipped' in result) {
        deps.onProgress({
          type: 'workflow_agent',
          agentId: agentIdRef,
          label,
          phaseTitle,
          state: 'error',
          error: 'skipped by user',
        })
        return null
      }

      const value = 'structured' in result ? result.structured : result.text
      deps.journal.record(prompt, opts, value)
      deps.onProgress({
        type: 'workflow_agent',
        agentId: result.agentId ?? agentIdRef,
        label,
        phaseTitle,
        state: 'done',
        tokens: result.tokens,
        toolCalls: result.toolCalls,
      })
      return value
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      deps.onProgress({
        type: 'workflow_agent',
        agentId: agentIdRef,
        label,
        phaseTitle,
        state: 'error',
        error: msg,
      })
      return null
    }
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/cli && bun test src/tools/WorkflowTool/agentCall.test.ts`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/cli/src/tools/WorkflowTool/agentCall.ts src/cli/src/tools/WorkflowTool/agentCall.test.ts
git commit -m "feat(cli): workflow agent() bridge (schema/skip/journal/progress)" -- src/cli/src/tools/WorkflowTool/agentCall.ts src/cli/src/tools/WorkflowTool/agentCall.test.ts
```

---

### Task 7: VM runtime assembly

**Files:**
- Create: `src/cli/src/tools/WorkflowTool/vmRuntime.ts`
- Test: `src/cli/src/tools/WorkflowTool/vmRuntime.test.ts`

Assembles the `node:vm` context: injects `agent`/`parallel`/`pipeline`/`phase`/`log`/`budget`/`args`/`workflow`, shadows the non-deterministic primitives to throw, denies codegen. Takes the `agent` fn (Task 6) and combinators (Task 4) as inputs so it stays testable.

- [ ] **Step 1: Write the failing test**

```typescript
// src/cli/src/tools/WorkflowTool/vmRuntime.test.ts
import { expect, test } from 'bun:test'
import { buildWorkflowContext, runScriptBody } from './vmRuntime.js'

function ctx(overrides: any = {}) {
  const logs: string[] = []
  const phases: string[] = []
  return buildWorkflowContext({
    agent: async (p: string) => `ran:${p}`,
    log: (m: string) => logs.push(m),
    phase: (t: string) => phases.push(t),
    getBudget: () => ({ total: null, spent: () => 0, remaining: () => Infinity }),
    args: { q: 'hi' },
    resolveWorkflow: async () => 'nested-result',
    _logs: logs, _phases: phases,
    ...overrides,
  })
}

test('script can call agent/parallel/pipeline and set result', async () => {
  const c = ctx()
  const body = `
    phase('Scan')
    const a = await agent('one')
    const b = await parallel([() => agent('p1'), () => agent('p2')])
    const d = await pipeline(['x'], it => agent(it))
    result = { a, b, d }
  `
  const r = await runScriptBody(body, c, { timeout: 2000 })
  expect(r).toEqual({ a: 'ran:one', b: ['ran:p1', 'ran:p2'], d: ['ran:x'] })
})

test('args is exposed verbatim', async () => {
  const c = ctx()
  const r = await runScriptBody(`result = args.q`, c, { timeout: 1000 })
  expect(r).toBe('hi')
})

test('Date.now / Math.random / new Date() throw inside the vm', async () => {
  const c = ctx()
  await expect(runScriptBody(`result = Date.now()`, c, { timeout: 1000 })).rejects.toThrow()
  await expect(runScriptBody(`result = Math.random()`, c, { timeout: 1000 })).rejects.toThrow()
  await expect(runScriptBody(`result = new Date()`, c, { timeout: 1000 })).rejects.toThrow()
})

test('new Date(arg) still works', async () => {
  const c = ctx()
  const r = await runScriptBody(`result = new Date(0).getUTCFullYear()`, c, { timeout: 1000 })
  expect(r).toBe(1970)
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cli && bun test src/tools/WorkflowTool/vmRuntime.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Write the implementation**

```typescript
// src/cli/src/tools/WorkflowTool/vmRuntime.ts
import vm from 'node:vm'
import { runParallel, runPipeline } from './combinators.js'

export type WorkflowBudget = {
  total: number | null
  spent: () => number
  remaining: () => number
}

export type WorkflowContextDeps = {
  agent: (prompt: string, opts?: Record<string, unknown>) => Promise<unknown>
  log: (message: string) => void
  phase: (title: string) => void
  getBudget: () => WorkflowBudget
  args: unknown
  resolveWorkflow: (name: string, args?: unknown) => Promise<unknown>
}

// A guarded Date: argless construction throws; Date(arg) is allowed.
function makeGuardedDate(): DateConstructor {
  const GuardedDate = function (this: unknown, ...a: unknown[]) {
    if (a.length === 0) {
      throw new Error(
        'new Date() is unavailable in workflow scripts (breaks resume). Pass timestamps via args.',
      )
    }
    // @ts-expect-error spread into Date
    return new Date(...a)
  } as unknown as DateConstructor
  GuardedDate.prototype = Date.prototype
  // Date.now / Date.parse / Date.UTC — deny now(), keep parse/UTC (deterministic).
  GuardedDate.parse = Date.parse
  GuardedDate.UTC = Date.UTC
  Object.defineProperty(GuardedDate, 'now', {
    value: () => {
      throw new Error(
        'Date.now() is unavailable in workflow scripts (breaks resume).',
      )
    },
  })
  return GuardedDate
}

function makeGuardedMath(): Math {
  const GuardedMath: Math = Object.create(Math)
  Object.defineProperty(GuardedMath, 'random', {
    value: () => {
      throw new Error(
        'Math.random() is unavailable in workflow scripts (breaks resume). Vary agent prompts by index instead.',
      )
    },
  })
  return GuardedMath
}

export function buildWorkflowContext(deps: WorkflowContextDeps): vm.Context {
  const sandbox: Record<string, unknown> = {
    __proto__: null,
    agent: deps.agent,
    parallel: runParallel,
    pipeline: runPipeline,
    phase: deps.phase,
    log: deps.log,
    console: { log: (...a: unknown[]) => deps.log(a.map(String).join(' ')) },
    budget: deps.getBudget(),
    args: deps.args,
    workflow: deps.resolveWorkflow,
    result: null,
    // Deterministic-safe built-ins pass through; the two hazards are shadowed.
    JSON,
    Math: makeGuardedMath(),
    Date: makeGuardedDate(),
    Promise,
    Array,
    Object,
    Set,
    Map,
    setTimeout,
    clearTimeout,
  }
  return vm.createContext(sandbox, {
    codeGeneration: { strings: false, wasm: false },
  })
}

// Wrap the body in an async IIFE, run it, await the returned promise, then
// read `result` off the context (upstream reads the assigned global).
export async function runScriptBody(
  body: string,
  context: vm.Context,
  opts: { timeout: number },
): Promise<unknown> {
  const src = `(async () => {\n${body}\n})()`
  const script = new vm.Script(src)
  const promise = script.runInContext(context, { timeout: opts.timeout })
  await promise
  return (context as Record<string, unknown>).result
}
```

Note: `codeGeneration:{strings:false}` blocks `eval`/`new Function` inside the script but NOT `new vm.Script` here (that's our own trusted compile). Verified working under Bun in the spec's feasibility test.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/cli && bun test src/tools/WorkflowTool/vmRuntime.test.ts`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/cli/src/tools/WorkflowTool/vmRuntime.ts src/cli/src/tools/WorkflowTool/vmRuntime.test.ts
git commit -m "feat(cli): workflow vm runtime (globals, determinism guards)" -- src/cli/src/tools/WorkflowTool/vmRuntime.ts src/cli/src/tools/WorkflowTool/vmRuntime.test.ts
```

---

### Task 8: Named-workflow loader

**Files:**
- Create: `src/cli/src/tools/WorkflowTool/namedWorkflows.ts`
- Test: `src/cli/src/tools/WorkflowTool/namedWorkflows.test.ts`

Loads `.md`-free `.mjs`/`.js` workflow scripts from `~/.claude/workflows/` (user) and `<cwd>/.claude/workflows/` (project, wins on name). Size-capped, meta-validated, results memoized with an explicit clear.

- [ ] **Step 1: Write the failing test**

```typescript
// src/cli/src/tools/WorkflowTool/namedWorkflows.test.ts
import { expect, test, beforeEach, afterEach } from 'bun:test'
import { mkdtempSync, writeFileSync, mkdirSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { loadWorkflowsFromDir, clearNamedWorkflowCache } from './namedWorkflows.js'

let dir: string
beforeEach(() => { dir = mkdtempSync(join(tmpdir(), 'wf-')); clearNamedWorkflowCache() })
afterEach(() => rmSync(dir, { recursive: true, force: true }))

test('loads a valid workflow file and reads meta', async () => {
  mkdirSync(join(dir, 'workflows'), { recursive: true })
  writeFileSync(join(dir, 'workflows', 'spec.mjs'),
    `export const meta = { name: 'spec', description: 'write a spec' }\nphase('go')`)
  const list = await loadWorkflowsFromDir(join(dir, 'workflows'), 'userSettings')
  expect(list).toHaveLength(1)
  expect(list[0]!.name).toBe('spec')
  expect(list[0]!.description).toBe('write a spec')
})

test('skips a file with invalid meta', async () => {
  mkdirSync(join(dir, 'workflows'), { recursive: true })
  writeFileSync(join(dir, 'workflows', 'bad.mjs'), `phase('no meta here')`)
  const list = await loadWorkflowsFromDir(join(dir, 'workflows'), 'userSettings')
  expect(list).toHaveLength(0)
})

test('missing dir returns empty', async () => {
  const list = await loadWorkflowsFromDir(join(dir, 'nope'), 'userSettings')
  expect(list).toEqual([])
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cli && bun test src/tools/WorkflowTool/namedWorkflows.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Write the implementation**

```typescript
// src/cli/src/tools/WorkflowTool/namedWorkflows.ts
import { readdir, readFile, stat } from 'node:fs/promises'
import { join } from 'node:path'
import { homedir } from 'node:os'
import { parseWorkflowMeta, type WorkflowMeta } from './meta.js'

const MAX_WORKFLOW_BYTES = 200_000

export type NamedWorkflow = {
  source: 'userSettings' | 'projectSettings'
  name: string
  description: string
  whenToUse?: string
  phases?: WorkflowMeta['phases']
  script: string
  filePath: string
}

let cache: Map<string, NamedWorkflow[]> | null = null

export function clearNamedWorkflowCache(): void {
  cache = null
}

export async function loadWorkflowsFromDir(
  dir: string,
  source: NamedWorkflow['source'],
): Promise<NamedWorkflow[]> {
  let names: string[]
  try {
    names = await readdir(dir)
  } catch {
    return []
  }
  const out: NamedWorkflow[] = []
  for (const file of names) {
    if (!file.endsWith('.mjs') && !file.endsWith('.js')) continue
    const path = join(dir, file)
    try {
      const s = await stat(path)
      if (s.size > MAX_WORKFLOW_BYTES) continue
      const script = await readFile(path, 'utf-8')
      const parsed = parseWorkflowMeta(script)
      if ('error' in parsed) continue
      out.push({
        source,
        name: parsed.meta.name,
        description: parsed.meta.description,
        whenToUse: parsed.meta.whenToUse,
        phases: parsed.meta.phases,
        script,
        filePath: path,
      })
    } catch {
      continue
    }
  }
  return out
}

// User dir + project dir; project wins on name collision. Memoized by cwd.
export async function getAllWorkflows(cwd: string): Promise<NamedWorkflow[]> {
  cache ??= new Map()
  const cached = cache.get(cwd)
  if (cached) return cached
  const [user, project] = await Promise.all([
    loadWorkflowsFromDir(join(homedir(), '.claude', 'workflows'), 'userSettings'),
    loadWorkflowsFromDir(join(cwd, '.claude', 'workflows'), 'projectSettings'),
  ])
  const byName = new Map<string, NamedWorkflow>()
  for (const w of user) byName.set(w.name, w)
  for (const w of project) byName.set(w.name, w)
  const list = [...byName.values()].sort((a, b) => a.name.localeCompare(b.name))
  cache.set(cwd, list)
  return list
}

export async function resolveWorkflowByName(
  name: string,
  cwd: string,
): Promise<NamedWorkflow | undefined> {
  return (await getAllWorkflows(cwd)).find(w => w.name === name)
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/cli && bun test src/tools/WorkflowTool/namedWorkflows.test.ts`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/cli/src/tools/WorkflowTool/namedWorkflows.ts src/cli/src/tools/WorkflowTool/namedWorkflows.test.ts
git commit -m "feat(cli): named-workflow loader (user + project dirs)" -- src/cli/src/tools/WorkflowTool/namedWorkflows.ts src/cli/src/tools/WorkflowTool/namedWorkflows.test.ts
```

---

### Task 9: Runner (orchestrates journal + vm + serialization)

**Files:**
- Create: `src/cli/src/tools/WorkflowTool/runWorkflow.ts`
- Test: `src/cli/src/tools/WorkflowTool/runWorkflow.test.ts`

Ties Tasks 3–8 together with an injected `dispatch` (real one wired in Task 10). Builds the agent fn + context, runs the body, races the abort signal, JSON-serializes the result (drops functions), returns `{result, agentCount, logs, failures, durationMs, error?}`.

- [ ] **Step 1: Write the failing test**

```typescript
// src/cli/src/tools/WorkflowTool/runWorkflow.test.ts
import { expect, test } from 'bun:test'
import { runWorkflow } from './runWorkflow.js'

const noBudget = () => ({ total: null as number | null, spent: () => 0, remaining: () => Infinity })

test('runs a script to completion and serializes result', async () => {
  const out = await runWorkflow({
    scriptBody: `const a = await agent('x'); result = { a }`,
    args: undefined,
    dispatch: async (p: string) => ({ text: `t:${p}`, tokens: 3, toolCalls: 1 }),
    getBudget: noBudget,
    resolveWorkflow: async () => null,
    onProgress: () => {},
    signal: new AbortController().signal,
    syncTimeoutMs: 2000,
  })
  expect(out.error).toBeUndefined()
  expect(out.result).toEqual({ a: 't:x' })
  expect(out.agentCount).toBe(1)
})

test('captures logs and failures', async () => {
  const out = await runWorkflow({
    scriptBody: `log('hi'); const a = await agent('boom'); result = a`,
    args: undefined,
    dispatch: async () => { throw new Error('dead') },
    getBudget: noBudget, resolveWorkflow: async () => null,
    onProgress: () => {}, signal: new AbortController().signal, syncTimeoutMs: 2000,
  })
  expect(out.logs).toContain('hi')
  expect(out.result).toBeNull()          // agent() returned null on failure
})

test('script error is captured, not thrown', async () => {
  const out = await runWorkflow({
    scriptBody: `throw new Error('script boom')`,
    args: undefined, dispatch: async () => ({ text: '', tokens: 0, toolCalls: 0 }),
    getBudget: noBudget, resolveWorkflow: async () => null,
    onProgress: () => {}, signal: new AbortController().signal, syncTimeoutMs: 2000,
  })
  expect(out.error).toContain('script boom')
})

test('abort rejects the run with a killed error', async () => {
  const ac = new AbortController()
  const p = runWorkflow({
    scriptBody: `await new Promise(r => setTimeout(r, 5000)); result = 1`,
    args: undefined, dispatch: async () => ({ text: '', tokens: 0, toolCalls: 0 }),
    getBudget: noBudget, resolveWorkflow: async () => null,
    onProgress: () => {}, signal: ac.signal, syncTimeoutMs: 10000,
  })
  ac.abort()
  const out = await p
  expect(out.error).toMatch(/abort/i)
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cli && bun test src/tools/WorkflowTool/runWorkflow.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Write the implementation**

```typescript
// src/cli/src/tools/WorkflowTool/runWorkflow.ts
import type { SdkWorkflowProgress } from '../../types/tools.js'
import { makeAgentFn, type Dispatch } from './agentCall.js'
import { WorkflowJournal, type JournalEntry } from './journal.js'
import { ConcurrencyLimiter, computeConcurrency } from './limiter.js'
import {
  buildWorkflowContext,
  runScriptBody,
  type WorkflowBudget,
} from './vmRuntime.js'

export type RunWorkflowInput = {
  scriptBody: string
  args: unknown
  dispatch: Dispatch
  getBudget: () => WorkflowBudget
  resolveWorkflow: (name: string, args?: unknown) => Promise<unknown>
  onProgress: (p: SdkWorkflowProgress) => void
  signal: AbortSignal
  syncTimeoutMs?: number
  priorJournal?: JournalEntry[]
}

export type RunWorkflowResult = {
  result: unknown
  agentCount: number
  logs: string[]
  failures: string[]
  durationMs: number
  error?: string
  journal: JournalEntry[]
}

const MAX_LOGS = 1000

export async function runWorkflow(
  input: RunWorkflowInput,
): Promise<RunWorkflowResult> {
  const startedAt = Date.now()
  const logs: string[] = []
  const failures: string[] = []
  let agentCount = 0
  let currentPhase: string | undefined

  const journal = input.priorJournal
    ? WorkflowJournal.fromEntries(input.priorJournal)
    : new WorkflowJournal()
  const limiter = new ConcurrencyLimiter(computeConcurrency())
  let seq = 0

  const onProgress = (p: SdkWorkflowProgress): void => {
    if (p.type === 'workflow_agent') {
      if (p.state === 'running') agentCount++
      if (p.state === 'error' && p.error && p.error !== 'skipped by user') {
        failures.push(`${p.label}: ${p.error}`)
      }
    }
    input.onProgress(p)
  }

  const agent = makeAgentFn({
    dispatch: input.dispatch,
    journal,
    limiter,
    onProgress,
    getPhase: () => currentPhase,
    nextIndex: () => seq++,
    signal: input.signal,
  })

  const context = buildWorkflowContext({
    agent: agent as (p: string, o?: Record<string, unknown>) => Promise<unknown>,
    log: (m: string) => {
      if (logs.length < MAX_LOGS) logs.push(m)
      onProgress({ type: 'workflow_log', message: m })
    },
    phase: (t: string) => {
      currentPhase = t
    },
    getBudget: input.getBudget,
    args: input.args,
    resolveWorkflow: input.resolveWorkflow,
  })

  try {
    const runPromise = runScriptBody(input.scriptBody, context, {
      timeout: input.syncTimeoutMs ?? 30_000,
    })
    const result = input.signal
      ? await Promise.race([
          runPromise,
          new Promise((_res, rej) => {
            if (input.signal.aborted) return rej(new Error('Workflow aborted'))
            input.signal.addEventListener('abort', () =>
              rej(new Error('Workflow aborted')),
            )
          }),
        ])
      : await runPromise

    // Serialize: drop functions, tolerate cycles by falling back to a plain
    // JSON round-trip that strips functions.
    let serialized: unknown
    try {
      serialized = JSON.parse(
        JSON.stringify(result, (_k, v) =>
          typeof v === 'function' ? undefined : v,
        ) ?? 'null',
      )
    } catch {
      serialized = null
    }
    return {
      result: serialized,
      agentCount,
      logs,
      failures,
      durationMs: Date.now() - startedAt,
      journal: journal.entries(),
    }
  } catch (e) {
    const raw = e instanceof Error ? (e.stack ?? e.message) : String(e)
    const trimmed = raw.split('\n').slice(0, 6).join('\n')
    return {
      result: null,
      agentCount,
      logs,
      failures,
      durationMs: Date.now() - startedAt,
      error: trimmed,
      journal: journal.entries(),
    }
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/cli && bun test src/tools/WorkflowTool/runWorkflow.test.ts`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/cli/src/tools/WorkflowTool/runWorkflow.ts src/cli/src/tools/WorkflowTool/runWorkflow.test.ts
git commit -m "feat(cli): workflow runner (journal+vm+serialize+abort race)" -- src/cli/src/tools/WorkflowTool/runWorkflow.ts src/cli/src/tools/WorkflowTool/runWorkflow.test.ts
```

---

### Task 10: Real `dispatch` — `runAgent` bridge

**Files:**
- Create: `src/cli/src/tools/WorkflowTool/dispatch.ts`
- Test: none (integration-only; exercised by the end-to-end smoke in Task 16 — a `bun test` for this needs a full ToolUseContext, which isn't available under the test harness by design).

This is the ONLY workflow module that imports `runAgent`. It reduces a `runAgent` message stream into a `DispatchResult`. It reuses the existing `finalizeAgentTool` + `extractTextContent` helpers and the `createSyntheticOutputTool` for schema mode.

- [ ] **Step 1: Read the reference call site**

Read `src/cli/src/tools/AgentTool/AgentTool.tsx:846-950` (the sync `runAgent` iteration + `finalizeAgentTool`) and `src/cli/src/tools/AgentTool/agentToolUtils.ts:276` (`finalizeAgentTool` signature) and `src/cli/src/tools/AgentTool/runAgent.ts:248-329` (the params object). Match their shapes.

- [ ] **Step 2: Write the implementation**

```typescript
// src/cli/src/tools/WorkflowTool/dispatch.ts
import type { ToolUseContext } from '../../Tool.js'
import type { AgentDefinition } from '../AgentTool/loadAgentsDir.js'
import { runAgent } from '../AgentTool/runAgent.js'
import { finalizeAgentTool, extractTextContent } from '../AgentTool/agentToolUtils.js'
import { createSyntheticOutputTool } from '../SyntheticOutputTool/SyntheticOutputTool.js'
import { createUserMessage } from '../../utils/messages.js'
import { assembleToolPool } from '../../tools.js'
import { createAgentId, type AgentId } from '../../types/ids.js'
import type { DispatchResult, AgentOpts } from './agentCall.js'
import { getWorkflowAgentDefinition } from './workflowAgentDef.js'

export type DispatchDeps = {
  toolUseContext: ToolUseContext
  defaultModel: string
  runId: string
  agentControllers?: Map<string, AbortController>
  resolveAgentType: (name: string) => AgentDefinition | undefined
}

export function makeDispatch(deps: DispatchDeps) {
  return async function dispatch(
    prompt: string,
    opts: AgentOpts,
    signal: AbortSignal,
  ): Promise<DispatchResult> {
    const agentId = createAgentId()
    const controller = new AbortController()
    // Chain the workflow-level signal so kill/skip aborts this agent.
    if (signal.aborted) controller.abort()
    else signal.addEventListener('abort', () => controller.abort())
    deps.agentControllers?.set(agentId, controller)

    // Base definition (built-in workflow subagent) or a named custom type.
    const baseDef = opts.agentType
      ? deps.resolveAgentType(opts.agentType) ?? getWorkflowAgentDefinition()
      : getWorkflowAgentDefinition()

    // Schema mode: append a StructuredOutput instruction + include the tool.
    const schemaTool = opts.schema
      ? createSyntheticOutputTool(opts.schema)
      : undefined
    if (schemaTool && 'error' in schemaTool) {
      deps.agentControllers?.delete(agentId)
      throw new Error(`Invalid schema: ${schemaTool.error}`)
    }

    const appState = deps.toolUseContext.getAppState()
    const workerTools = assembleToolPool(
      { ...appState.toolPermissionContext, mode: 'acceptEdits' },
      appState.mcp.tools,
    )
    const availableTools =
      schemaTool && 'tool' in schemaTool
        ? [...workerTools, schemaTool.tool]
        : workerTools

    const messages: Awaited<ReturnType<typeof finalizeAgentTool>>['content'] extends never
      ? never
      : unknown[] = []
    const collected: unknown[] = []

    try {
      for await (const msg of runAgent({
        agentDefinition: baseDef,
        promptMessages: [createUserMessage({ content: buildPrompt(prompt, opts) })],
        toolUseContext: deps.toolUseContext,
        canUseTool: async () => ({ behavior: 'allow', updatedInput: {} }),
        isAsync: true,
        querySource: 'workflow' as never,
        model: (opts.model ?? deps.defaultModel) as never,
        availableTools: availableTools as never,
        worktreePath: undefined,
        description: opts.label ?? prompt.slice(0, 60),
        transcriptSubdir: `workflows/${deps.runId}`,
        override: { agentId: agentId as AgentId, abortController: controller },
      } as never)) {
        collected.push(msg)
      }
    } finally {
      deps.agentControllers?.delete(agentId)
    }

    if (controller.signal.aborted && signal.aborted) {
      return { skipped: true }
    }

    const finalized = finalizeAgentTool(collected as never, agentId, {
      prompt,
      startTime: Date.now(),
      agentType: baseDef.agentType,
    } as never)

    // Schema mode: pull the structured_output from the StructuredOutput call.
    if (opts.schema) {
      const structured = extractStructuredOutput(collected)
      if (structured !== undefined) {
        return {
          structured,
          tokens: finalized.totalTokens ?? 0,
          toolCalls: finalized.totalToolUseCount ?? 0,
          agentId,
        }
      }
    }

    return {
      text: extractTextContent(finalized.content, '\n'),
      tokens: finalized.totalTokens ?? 0,
      toolCalls: finalized.totalToolUseCount ?? 0,
      agentId,
    }
  }
}

function buildPrompt(prompt: string, opts: AgentOpts): string {
  if (!opts.schema) return prompt
  return `${prompt}\n\nRespond by calling the StructuredOutput tool with a value matching the required schema. Your final text is ignored in schema mode.`
}

// Walk collected messages for the last StructuredOutput tool result payload.
function extractStructuredOutput(messages: unknown[]): unknown {
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i] as { toolUseResult?: { structured_output?: unknown } }
    if (m?.toolUseResult?.structured_output !== undefined) {
      return m.toolUseResult.structured_output
    }
  }
  return undefined
}
```

> **Worker note:** the exact field names on `finalizeAgentTool`'s return (`totalTokens`, `totalToolUseCount`, `content`) and the `structured_output` location come from `agentToolUtils.ts` + `SyntheticOutputTool.ts`. Read both before finalizing; adjust the accessors to the real shapes (the `as never` casts are placeholders for the run-from-source artifact style — replace with the real `Parameters<typeof runAgent>[0]` shape from AgentTool.tsx:603). This module is verified by the Task 16 smoke, not a unit test.

- [ ] **Step 3: Create the built-in workflow agent definition**

```typescript
// src/cli/src/tools/WorkflowTool/workflowAgentDef.ts
import type { AgentDefinition } from '../AgentTool/loadAgentsDir.js'

// Minimal built-in subagent used for workflow agent() calls without an
// explicit agentType. Mirrors the general-purpose worker: full tool access,
// told its final text IS the return value.
export function getWorkflowAgentDefinition(): AgentDefinition {
  return {
    agentType: 'workflow',
    whenToUse: 'Internal workflow subagent',
    systemPrompt:
      'You are a workflow subagent. Do exactly the task described and return the result. Your FINAL message text is consumed as the return value of this step — return raw data (or, in schema mode, call StructuredOutput). Do not address a human.',
    tools: undefined,
    source: 'built-in',
    model: undefined,
  } as AgentDefinition
}
```

- [ ] **Step 4: Parse + import-resolve check**

Run: `cd src/cli && bun build src/tools/WorkflowTool/workflowAgentDef.ts --no-bundle && bun build src/tools/WorkflowTool/dispatch.ts --no-bundle`
Expected: both compile. If `dispatch.ts` fails on an import path, fix the path against the real file (do NOT whole-graph bundle).

- [ ] **Step 5: Commit**

```bash
git add src/cli/src/tools/WorkflowTool/dispatch.ts src/cli/src/tools/WorkflowTool/workflowAgentDef.ts
git commit -m "feat(cli): workflow runAgent dispatch bridge + built-in workflow agent" -- src/cli/src/tools/WorkflowTool/dispatch.ts src/cli/src/tools/WorkflowTool/workflowAgentDef.ts
```

---

### Task 11: Extend `LocalWorkflowTask` state + skip/kill

**Files:**
- Modify: `src/cli/src/tasks/LocalWorkflowTask/LocalWorkflowTask.ts`
- Test: `src/cli/src/tasks/LocalWorkflowTask/LocalWorkflowTask.test.ts`

Extend the state shape and make skip actually abort an agent controller; kill aborts the run + all agents.

- [ ] **Step 1: Write the failing test**

```typescript
// src/cli/src/tasks/LocalWorkflowTask/LocalWorkflowTask.test.ts
import { expect, test } from 'bun:test'
import { skipWorkflowAgent, killWorkflowTask } from './LocalWorkflowTask.js'

function fakeState(taskId: string, controllers: Map<string, AbortController>) {
  return {
    tasks: {
      [taskId]: {
        id: taskId, type: 'local_workflow', status: 'running',
        description: 'wf', startTime: Date.now(), outputFile: '', outputOffset: 0,
        notified: false, agentCount: 1, agentControllers: controllers,
      },
    },
  }
}

test('skip aborts the named agent controller', () => {
  const ctrl = new AbortController()
  const controllers = new Map([['a1', ctrl]])
  let state: any = fakeState('w1', controllers)
  const setAppState = (f: any) => { state = f(state) }
  skipWorkflowAgent('w1', 'a1', setAppState)
  expect(ctrl.signal.aborted).toBe(true)
})

test('kill aborts the run controller and marks killed', () => {
  const runCtrl = new AbortController()
  const controllers = new Map<string, AbortController>()
  let state: any = fakeState('w2', controllers)
  state.tasks['w2'].runController = runCtrl
  const setAppState = (f: any) => { state = f(state) }
  killWorkflowTask('w2', setAppState)
  expect(runCtrl.signal.aborted).toBe(true)
  expect(state.tasks['w2'].status).toBe('killed')
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cli && bun test src/tasks/LocalWorkflowTask/LocalWorkflowTask.test.ts`
Expected: FAIL — skip is a no-op, no `runController`.

- [ ] **Step 3: Rewrite the module**

```typescript
// src/cli/src/tasks/LocalWorkflowTask/LocalWorkflowTask.ts
import type { SetAppState, Task, TaskStateBase } from '../../Task.js'
import type { SdkWorkflowProgress } from '../../types/tools.js'
import type { WorkflowPhase } from '../../tools/WorkflowTool/meta.js'
import { updateTaskState } from '../../utils/task/framework.js'

export type LocalWorkflowTaskState = TaskStateBase & {
  type: 'local_workflow'
  workflowName?: string
  workflowRunId?: string
  summary?: string
  title?: string
  prompt?: string
  phases?: WorkflowPhase[]
  workflowProgress?: SdkWorkflowProgress[]
  totalTokens?: number
  totalToolCalls?: number
  agentCount: number
  // Non-reactive: Map identity is stable; mutating it never re-renders. Same
  // pattern as sessionHooks agentControllers.
  agentControllers?: Map<string, AbortController>
  runController?: AbortController
}

function markKilled(taskId: string, setAppState: SetAppState): void {
  updateTaskState<LocalWorkflowTaskState>(taskId, setAppState, task => {
    if (task.status !== 'running' && task.status !== 'pending') return task
    task.runController?.abort()
    if (task.agentControllers) {
      for (const c of task.agentControllers.values()) c.abort()
    }
    return { ...task, status: 'killed', endTime: Date.now(), notified: true }
  })
}

export const LocalWorkflowTask: Task = {
  name: 'LocalWorkflowTask',
  type: 'local_workflow',
  async kill(taskId, setAppState) {
    markKilled(taskId, setAppState)
  },
}

export function killWorkflowTask(taskId: string, setAppState: SetAppState): void {
  markKilled(taskId, setAppState)
}

// Abort just this agent's controller; its agent() call resolves null with
// state 'skipped by user'. Mutates the (non-reactive) Map, returns task
// unchanged so no re-render churn.
export function skipWorkflowAgent(
  taskId: string,
  agentId: string,
  setAppState: SetAppState,
): void {
  updateTaskState<LocalWorkflowTaskState>(taskId, setAppState, task => {
    task.agentControllers?.get(agentId)?.abort()
    return task
  })
}

// Retry is journal-resume based (post-run), surfaced in the detail dialog as
// a resume hint rather than a live control. No-op kept for the props contract.
export function retryWorkflowAgent(
  _taskId: string,
  _agentId: string,
  _setAppState: SetAppState,
): void {}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/cli && bun test src/tasks/LocalWorkflowTask/LocalWorkflowTask.test.ts`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/cli/src/tasks/LocalWorkflowTask/LocalWorkflowTask.ts src/cli/src/tasks/LocalWorkflowTask/LocalWorkflowTask.test.ts
git commit -m "feat(cli): LocalWorkflowTask state + real skip/kill" -- src/cli/src/tasks/LocalWorkflowTask/LocalWorkflowTask.ts src/cli/src/tasks/LocalWorkflowTask/LocalWorkflowTask.test.ts
```

---

### Task 12: Tool prompt (verbatim upstream)

**Files:**
- Modify: `src/cli/src/tools/WorkflowTool/prompt.ts`

- [ ] **Step 1: Extract the upstream prompt**

Run this to dump the full prompt text to a scratch file:

```bash
python3 - <<'EOF'
data = open('/home/ulrich/.npm-global/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe','rb').read()
start = data.find(b'Execute a workflow script that orchestrates multiple subagents deterministically. Workflows run in the background')
end = start
run = 0
while end < len(data):
    b = data[end]
    if b in (9,10) or 0x20 <= b <= 0x7e or b >= 0x80:
        run = 0
    else:
        run += 1
        if run >= 3:
            end -= 2; break
    end += 1
open('/tmp/wf-prompt.txt','w').write(data[start:end].decode('utf-8','replace'))
print('wrote', end-start, 'bytes')
EOF
```

- [ ] **Step 2: Write `prompt.ts`**

Replace `src/cli/src/tools/WorkflowTool/prompt.ts` with the extracted text as a template-literal export, **removing jarvis-inapplicable bits**: keep the opt-in gating language, keep all API docs / patterns / caps / resume sections; DELETE the "Ultracode" paragraph (jarvis has no ultracode session mode) and any `remote:true`/CCR mention. Structure:

```typescript
// src/cli/src/tools/WorkflowTool/prompt.ts
// Verbatim from Claude Code 2.1.170 Workflow tool prompt, with the ultracode
// session-mode paragraph and remote/CCR references removed (not applicable to
// the self-hosted jarvis build). See spec 2026-07-01.
export const WORKFLOW_TOOL_PROMPT = `Execute a workflow script that orchestrates multiple subagents deterministically. Workflows run in the background — this tool returns immediately with a task ID, and a <task-notification> arrives when the workflow completes. Use /workflows to watch live progress.

<... full extracted text, ultracode + remote paragraphs removed, backticks/backslashes escaped ...>`
```

- [ ] **Step 3: Parse check**

Run: `cd src/cli && bun build src/tools/WorkflowTool/prompt.ts --no-bundle`
Expected: compiles (watch for un-escaped backticks in the template literal — escape every `` ` `` and `${` in the extracted text).

- [ ] **Step 4: Commit**

```bash
git add src/cli/src/tools/WorkflowTool/prompt.ts
git commit -m "feat(cli): verbatim upstream Workflow tool prompt (ultracode/remote stripped)" -- src/cli/src/tools/WorkflowTool/prompt.ts
```

---

### Task 13: Real `WorkflowTool.ts`

**Files:**
- Modify: `src/cli/src/tools/WorkflowTool/WorkflowTool.ts`
- Test: `src/cli/src/tools/WorkflowTool/WorkflowTool.test.ts` (validateInput only — call() is integration)

Replace the graceful stub. `validateInput` runs the meta parse + determinism guard (testable). `call()` resolves script, syntax-prechecks, registers a `local_workflow` task, launches `runWorkflow` in the background via `makeDispatch`, returns `async_launched` immediately.

- [ ] **Step 1: Write the failing test (validateInput)**

```typescript
// src/cli/src/tools/WorkflowTool/WorkflowTool.test.ts
import { expect, test } from 'bun:test'
import { validateWorkflowScript } from './WorkflowTool.js'

test('accepts a valid deterministic script', () => {
  const r = validateWorkflowScript(
    `export const meta = { name: 'x', description: 'd' }\nconst a = await agent('p')`,
  )
  expect(r.ok).toBe(true)
})

test('rejects a non-deterministic script', () => {
  const r = validateWorkflowScript(
    `export const meta = { name: 'x', description: 'd' }\nconst t = Date.now()`,
  )
  expect(r.ok).toBe(false)
  if (r.ok) return
  expect(r.error).toMatch(/deterministic/i)
})

test('rejects a script with bad meta', () => {
  const r = validateWorkflowScript(`const a = await agent('p')`)
  expect(r.ok).toBe(false)
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cli && bun test src/tools/WorkflowTool/WorkflowTool.test.ts`
Expected: FAIL — `validateWorkflowScript` not exported.

- [ ] **Step 3: Rewrite `WorkflowTool.ts`**

Full rewrite. Read the extracted upstream `call()`/`validateInput` logic (dumped earlier in this session) for exact behavior. Key points:
- export a pure `validateWorkflowScript(script)` helper for the unit test + reuse in `validateInput`.
- `inputSchema`: `script?`, `name?`, `scriptPath?`, `args?` (z.unknown), `resumeFromRunId?` (regex `/^wf_[a-z0-9-]{6,}$/`), `description?`/`title?` ignored; `.refine` at least one of script|name|scriptPath.
- `isReadOnly()` → `false`; `isEnabled()` → `true`.
- `prompt()`/`description()` → `WORKFLOW_TOOL_PROMPT` (Task 12).
- `checkPermissions`: name-scoped allow/deny/ask; default ask "Review dynamic workflow before running" with resolved script in `updatedInput`; named workflows add an allow-suggestion (rule content = name).
- `call()`:
  1. resolve script (scriptPath > name > script),
  2. `parseWorkflowMeta` (throw WorkflowInputError on error),
  3. `new vm.Script` syntax precheck → on failure return `{status:'async_launched', taskId, error}`,
  4. `runId = resumeFromRunId ?? 'wf_'+randomUUID().slice(0,12)`,
  5. persist script to `<sessionDir>/workflows/<runId>/script.mjs`,
  6. register `local_workflow` task (generateTaskId, createTaskStateBase, registerTask) with `runController`, empty `agentControllers` Map, phases, summary=meta.description, title,
  7. fire the background runner (`void (async () => { const out = await runWorkflow({dispatch: makeDispatch(...), ...}); … update task status completed/failed/killed, enqueue task-notification, persist journal to <runId>/journal.jsonl })()`),
  8. return `{data:{status:'async_launched', taskId, taskType:'local_workflow', workflowName, runId, summary, transcriptDir, scriptPath}}`.
- `mapToolResultToToolResultBlockParam`: mirror upstream text ("Workflow launched in background. Task ID: … Use /workflows to watch live progress." + scriptPath/resume hints; error → is_error true).

Use these already-verified imports:
`buildTool`/`ToolDef` from `../../Tool.js`; `lazySchema` from `../../utils/lazySchema.js`; `z` from `zod/v4`; `parseWorkflowMeta`/`checkDeterminism` from `./meta.js`; `runWorkflow` from `./runWorkflow.js`; `makeDispatch` from `./dispatch.js`; `resolveWorkflowByName`/`getAllWorkflows` from `./namedWorkflows.js`; `WORKFLOW_TOOL_PROMPT` from `./prompt.js`; `generateTaskId`/`createTaskStateBase` from `../../Task.js`; `registerTask` from `../../utils/task/framework.js`; `getSessionProjectDir`/`getSessionId` from `../../utils/sessionStorage.js`.

```typescript
// exported helper the unit test targets
export function validateWorkflowScript(
  script: string,
): { ok: true } | { ok: false; error: string } {
  const parsed = parseWorkflowMeta(script)
  if ('error' in parsed) return { ok: false, error: parsed.error }
  if (!checkDeterminism(parsed.scriptBody)) {
    return {
      ok: false,
      error:
        'Workflow scripts must be deterministic: Date.now()/Math.random()/new Date() are unavailable (breaks resume). Stamp results after the workflow returns, or pass timestamps via args.',
    }
  }
  return { ok: true }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/cli && bun test src/tools/WorkflowTool/WorkflowTool.test.ts`
Expected: PASS (3 tests).

- [ ] **Step 5: Parse + import-resolve the whole tool**

Run: `cd src/cli && bun build src/tools/WorkflowTool/WorkflowTool.ts --no-bundle`
Expected: compiles; fix any wrong import path against the real files.

- [ ] **Step 6: Commit**

```bash
git add src/cli/src/tools/WorkflowTool/WorkflowTool.ts src/cli/src/tools/WorkflowTool/WorkflowTool.test.ts
git commit -m "feat(cli): real WorkflowTool (validate, permissions, background launch)" -- src/cli/src/tools/WorkflowTool/WorkflowTool.ts src/cli/src/tools/WorkflowTool/WorkflowTool.test.ts
```

---

### Task 14: `/workflows` listing + slash commands + permission dialog + detail dialog

**Files:**
- Modify: `src/cli/src/commands/workflows/workflows.ts`
- Modify: `src/cli/src/tools/WorkflowTool/createWorkflowCommand.ts`
- Modify: `src/cli/src/tools/WorkflowTool/WorkflowPermissionRequest.tsx`
- Modify: `src/cli/src/components/tasks/WorkflowDetailDialog.tsx`

- [ ] **Step 1: `/workflows` listing**

```typescript
// src/cli/src/commands/workflows/workflows.ts
import type { LocalCommandResult } from '../../commands.js'
import { getCwd } from '../../utils/state.js'
import { getAllWorkflows } from '../../tools/WorkflowTool/namedWorkflows.js'

export async function call(): Promise<LocalCommandResult> {
  const workflows = await getAllWorkflows(getCwd())
  if (workflows.length === 0) {
    return {
      type: 'text',
      value:
        'No named workflows found. Add scripts to ~/.claude/workflows/ or .claude/workflows/, or ask me to "run a workflow" with an inline script.',
    }
  }
  const lines = workflows.map(
    w => `  ${w.name} — ${w.description}${w.whenToUse ? ` (${w.whenToUse})` : ''} [${w.source === 'projectSettings' ? 'project' : 'user'}]`,
  )
  return { type: 'text', value: `Named workflows:\n${lines.join('\n')}` }
}
```

> Verify `getCwd` import path: `grep -rn "export function getCwd" src/cli/src/utils/state.ts`. If it lives elsewhere, use the real path.

- [ ] **Step 2: Named workflows → slash commands**

```typescript
// src/cli/src/tools/WorkflowTool/createWorkflowCommand.ts
import type { Command } from '../../commands.js'
import { getAllWorkflows } from './namedWorkflows.js'

// Each named workflow becomes a prompt-type slash command that instructs the
// model to invoke Workflow({name}). $ARGUMENTS flows into the workflow args.
export async function getWorkflowCommands(cwd: string): Promise<Command[]> {
  const workflows = await getAllWorkflows(cwd)
  return workflows.map(w => ({
    type: 'prompt',
    name: w.name,
    description: w.description,
    isEnabled: true,
    isHidden: false,
    progressMessage: `running workflow ${w.name}`,
    userFacingName: () => w.name,
    async getPromptForCommand(args: string) {
      return [
        {
          role: 'user' as const,
          content: `Run the "${w.name}" workflow via the Workflow tool: Workflow({ name: ${JSON.stringify(w.name)}${args ? `, args: ${JSON.stringify({ input: args })}` : ''} }).`,
        },
      ]
    },
  })) as unknown as Command[]
}
```

> Verify the `Command` prompt-variant shape: `grep -n "type: 'prompt'" src/cli/src/commands.ts` and read the surrounding object. Match its exact required fields (getPromptForCommand signature, etc.); adjust if the real shape differs.

- [ ] **Step 3: Permission dialog**

```tsx
// src/cli/src/tools/WorkflowTool/WorkflowPermissionRequest.tsx
import { Box, Text } from 'ink'
import * as React from 'react'
import { parseWorkflowMeta } from './meta.js'
import { PermissionRequestActions } from '../../components/permissions/PermissionRequestActions.js'

type Props = {
  toolUseConfirm: { input: { script?: string; name?: string } }
  onDone: () => void
  onReject: () => void
  verbose?: boolean
}

export function WorkflowPermissionRequest({ toolUseConfirm, onDone, onReject }: Props): React.ReactNode {
  const script = toolUseConfirm.input.script ?? ''
  const parsed = parseWorkflowMeta(script)
  const meta = 'error' in parsed ? null : parsed.meta
  return (
    <Box flexDirection="column" borderStyle="round" paddingX={1}>
      <Text bold>Review dynamic workflow before running</Text>
      {meta && <Text>{meta.name} — {meta.description}</Text>}
      {meta?.phases?.map((p, i) => <Text key={i} dimColor>  • {p.title}</Text>)}
      <Box marginTop={1} flexDirection="column">
        <Text dimColor>{script.slice(0, 1500)}{script.length > 1500 ? '\n… (truncated)' : ''}</Text>
      </Box>
      <PermissionRequestActions onAllow={onDone} onReject={onReject} />
    </Box>
  )
}
```

> Verify `PermissionRequestActions` exists and its props: `grep -rn "PermissionRequestActions\|export function.*PermissionRequest" src/cli/src/components/permissions/`. If the shared actions component differs, follow the pattern used by an existing real permission request (e.g. `AskUserQuestionPermissionRequest`).

- [ ] **Step 4: Detail dialog**

```tsx
// src/cli/src/components/tasks/WorkflowDetailDialog.tsx
import { Box, Text, useInput } from 'ink'
import * as React from 'react'
import type { LocalWorkflowTaskState } from '../../tasks/LocalWorkflowTask/LocalWorkflowTask.js'

type Props = {
  workflow: LocalWorkflowTaskState
  onDone: (msg: string, opts?: { display?: string }) => void
  onKill?: () => void
  onSkipAgent?: (agentId: string) => void
  onRetryAgent?: (agentId: string) => void
  onBack: () => void
}

export function WorkflowDetailDialog({ workflow, onKill, onSkipAgent, onBack }: Props): React.ReactNode {
  const progress = (workflow.workflowProgress ?? []).filter(
    (p): p is Extract<typeof p, { type: 'workflow_agent' }> => p.type === 'workflow_agent',
  )
  const [sel, setSel] = React.useState(0)
  useInput((input, key) => {
    if (key.leftArrow || key.escape) return onBack()
    if (key.upArrow) setSel(s => Math.max(0, s - 1))
    if (key.downArrow) setSel(s => Math.min(progress.length - 1, s + 1))
    if (input === 's' && onSkipAgent && progress[sel]) onSkipAgent(progress[sel].agentId)
    if (input === 'x' && onKill) onKill()
  })
  const glyph = (state: string) => (state === 'done' ? '✓' : state === 'error' ? '✗' : '●')
  return (
    <Box flexDirection="column" borderStyle="round" paddingX={1}>
      <Text bold>{workflow.title ?? workflow.workflowName ?? 'Workflow'} — {workflow.status}</Text>
      {workflow.summary && <Text dimColor>{workflow.summary}</Text>}
      <Box flexDirection="column" marginTop={1}>
        {progress.length === 0 && <Text dimColor>No agent activity yet…</Text>}
        {progress.map((p, i) => (
          <Text key={`${p.agentId}-${i}`} inverse={i === sel}>
            {glyph(p.state)} {p.phaseTitle ? `[${p.phaseTitle}] ` : ''}{p.label}
            {p.tokens ? ` · ${p.tokens}tok` : ''}{p.error ? ` · ${p.error}` : ''}
          </Text>
        ))}
      </Box>
      {workflow.status !== 'running' && workflow.workflowRunId && (
        <Text dimColor marginTop={1}>Resume: Workflow({'{'} scriptPath, resumeFromRunId: "{workflow.workflowRunId}" {'}'})</Text>
      )}
      <Text dimColor>↑/↓ select · s skip · x stop · ←/Esc back</Text>
    </Box>
  )
}
```

- [ ] **Step 5: Parse check all four**

Run: `cd src/cli && for f in commands/workflows/workflows.ts tools/WorkflowTool/createWorkflowCommand.ts tools/WorkflowTool/WorkflowPermissionRequest.tsx components/tasks/WorkflowDetailDialog.tsx; do bun build src/$f --no-bundle >/dev/null && echo "OK $f" || echo "FAIL $f"; done`
Expected: four `OK` lines.

- [ ] **Step 6: Commit**

```bash
git add src/cli/src/commands/workflows/workflows.ts src/cli/src/tools/WorkflowTool/createWorkflowCommand.ts src/cli/src/tools/WorkflowTool/WorkflowPermissionRequest.tsx src/cli/src/components/tasks/WorkflowDetailDialog.tsx
git commit -m "feat(cli): /workflows listing, slash commands, permission + detail dialogs" -- src/cli/src/commands/workflows/workflows.ts src/cli/src/tools/WorkflowTool/createWorkflowCommand.ts src/cli/src/tools/WorkflowTool/WorkflowPermissionRequest.tsx src/cli/src/components/tasks/WorkflowDetailDialog.tsx
```

---

### Task 15: Wire progress batching into the task (integration glue)

**Files:**
- Modify: `src/cli/src/tools/WorkflowTool/WorkflowTool.ts` (the background runner block from Task 13)

The `onProgress` passed to `runWorkflow` must batch (~16ms) into `updateTaskState` (append to `workflowProgress`, bump `totalTokens`/`totalToolCalls`) and call `emitTaskProgress({...workflowProgress})`. This is glue over Task 9's callback.

- [ ] **Step 1: Add the batching onProgress in the runner block**

```typescript
// inside WorkflowTool.call(), before firing runWorkflow:
import { updateTaskState } from '../../utils/task/framework.js'
import { emitTaskProgress } from '../../utils/task/sdkProgress.js'
import type { LocalWorkflowTaskState } from '../../tasks/LocalWorkflowTask/LocalWorkflowTask.js'

let pending: SdkWorkflowProgress[] = []
let flushTimer: ReturnType<typeof setTimeout> | undefined
const flush = () => {
  flushTimer = undefined
  if (pending.length === 0) return
  const batch = pending
  pending = []
  updateTaskState<LocalWorkflowTaskState>(taskId, setAppStateForTasks, task => {
    const merged = [...(task.workflowProgress ?? []), ...batch]
    const addTokens = batch.reduce((n, p) => n + (p.type === 'workflow_agent' ? (p.tokens ?? 0) : 0), 0)
    const addCalls = batch.reduce((n, p) => n + (p.type === 'workflow_agent' ? (p.toolCalls ?? 0) : 0), 0)
    return {
      ...task,
      workflowProgress: merged,
      totalTokens: (task.totalTokens ?? 0) + addTokens,
      totalToolCalls: (task.totalToolCalls ?? 0) + addCalls,
    }
  })
  const lastAgent = [...batch].reverse().find(p => p.type === 'workflow_agent')
  emitTaskProgress({
    taskId,
    toolUseId,
    description: lastAgent && lastAgent.type === 'workflow_agent' ? lastAgent.label : summary,
    startTime,
    totalTokens: 0,
    toolUses: 0,
    summary,
    workflowProgress: batch,
  })
}
const onProgress = (p: SdkWorkflowProgress) => {
  pending.push(p)
  flushTimer ??= setTimeout(flush, 16)
}
```

> `setAppStateForTasks` — use `toolUseContext.setAppStateForTasks ?? toolUseContext.setAppState` (the always-shared channel; see `runAgent.ts:337`).

- [ ] **Step 2: Parse check**

Run: `cd src/cli && bun build src/tools/WorkflowTool/WorkflowTool.ts --no-bundle`
Expected: compiles.

- [ ] **Step 3: Run the Part-1 unit suite**

Run: `cd src/cli && bun test src/tools/WorkflowTool/ src/tasks/LocalWorkflowTask/`
Expected: all green.

- [ ] **Step 4: Commit**

```bash
git add src/cli/src/tools/WorkflowTool/WorkflowTool.ts
git commit -m "feat(cli): batch workflow progress into task state + sdk events" -- src/cli/src/tools/WorkflowTool/WorkflowTool.ts
```

---

### Task 16: Enable `WORKFLOW_SCRIPTS` + end-to-end smoke

**Files:**
- Modify: `src/cli/scripts/start.sh`

- [ ] **Step 1: Full suite before flipping the flag**

Run: `cd src/cli && bun test`
Expected: existing 201 + new workflow tests all pass. Fix anything red BEFORE touching start.sh.

- [ ] **Step 2: Add the flag**

In `src/cli/scripts/start.sh`, add `--feature=WORKFLOW_SCRIPTS` to the feature list (alongside the existing `--feature=…` flags on the `bun` invocation).

- [ ] **Step 3: Boot check (the load-bearing one)**

Run: `cd /home/ulrich/Documents/Projects/jarvis && bin/jarvis -p "say OK"`
Expected: returns "OK" (or similar) and exits 0 within a few seconds. If it HANGS, the flag pulled a broken require — bisect: remove the flag, confirm boot returns, then fix the import that throws during tool assembly.

- [ ] **Step 4: End-to-end workflow smoke**

Run: `cd /home/ulrich/Documents/Projects/jarvis && bin/jarvis -p 'Use a workflow to run two agents in parallel that each return the word "pong", then return both.'`
Expected: the model calls Workflow; a task launches; completion notification returns two "pong"s. If the model declines (opt-in gating), the phrasing "use a workflow" satisfies the gate — confirm the tool was invoked. Capture any dispatch error and fix `dispatch.ts` accessors.

- [ ] **Step 5: Commit**

```bash
git add src/cli/scripts/start.sh
git commit -m "feat(cli): enable WORKFLOW_SCRIPTS feature flag" -- src/cli/scripts/start.sh
```

---

# PART 2 — HISTORY SNIP

### Task 17: Snip projection (boundary detection + view filtering)

**Files:**
- Modify: `src/cli/src/services/compact/snipProjection.ts`
- Test: `src/cli/src/services/compact/snipProjection.test.ts`

Snipped-ness is derived statelessly: a message is snipped iff its uuid is listed in some `snip_boundary` message's `removedUuids` within the same list. This must produce boundaries in the exact shape `sessionStorage.ts::applySnipRemovals` already reads (`snipMetadata.removedUuids`).

- [ ] **Step 1: Write the failing test**

```typescript
// src/cli/src/services/compact/snipProjection.test.ts
import { expect, test } from 'bun:test'
import { isSnipBoundaryMessage, projectSnippedView, projectSnipMessages } from './snipProjection.js'

const boundary = (removed: string[]) => ({
  type: 'system', subtype: 'snip_boundary', uuid: 'b1', isMeta: false,
  content: 'snipped', snipMetadata: { removedUuids: removed, tokensFreed: 100, label: 'x' },
})
const msg = (uuid: string) => ({ type: 'user', uuid, message: { role: 'user', content: 'hi' } })

test('isSnipBoundaryMessage detects the subtype', () => {
  expect(isSnipBoundaryMessage(boundary([]) as any)).toBe(true)
  expect(isSnipBoundaryMessage(msg('u1') as any)).toBe(false)
})

test('projectSnippedView removes messages named in a boundary, keeps the boundary', () => {
  const list = [msg('u1'), msg('u2'), boundary(['u1']), msg('u3')] as any[]
  const out = projectSnippedView(list)
  const uuids = out.map(m => (m as any).uuid)
  expect(uuids).not.toContain('u1')
  expect(uuids).toContain('u2')
  expect(uuids).toContain('u3')
  expect(uuids).toContain('b1')       // boundary itself survives projection
})

test('projectSnipMessages is the same projection', () => {
  const list = [msg('u1'), boundary(['u1'])] as any[]
  expect(projectSnipMessages(list).map(m => (m as any).uuid)).toEqual(
    projectSnippedView(list).map(m => (m as any).uuid),
  )
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cli && bun test src/services/compact/snipProjection.test.ts`
Expected: FAIL — current stub returns messages unchanged / `isSnipBoundaryMessage` always false.

- [ ] **Step 3: Rewrite the module**

```typescript
// src/cli/src/services/compact/snipProjection.ts
import type { Message } from '../../types/message.js'

type SnipBoundary = {
  type: 'system'
  subtype: 'snip_boundary'
  snipMetadata?: { removedUuids?: string[]; tokensFreed?: number; label?: string }
}

export function isSnipBoundaryMessage(message: Message): boolean {
  const m = message as unknown as SnipBoundary
  return m?.type === 'system' && m.subtype === 'snip_boundary'
}

// Collect every uuid named by any snip boundary in this list.
function collectSnipped(messages: Message[]): Set<string> {
  const removed = new Set<string>()
  for (const m of messages) {
    if (!isSnipBoundaryMessage(m)) continue
    const uuids = (m as unknown as SnipBoundary).snipMetadata?.removedUuids ?? []
    for (const u of uuids) removed.add(u)
  }
  return removed
}

// Drop snipped messages; KEEP the boundary markers themselves.
export function projectSnippedView(messages: Message[]): Message[] {
  const removed = collectSnipped(messages)
  if (removed.size === 0) return messages
  return messages.filter(m => {
    const uuid = (m as unknown as { uuid?: string }).uuid
    return !uuid || !removed.has(uuid)
  })
}

// Alias — the model-facing SDK path and the projection path want the same
// filtering; no distinct behavior was found across the call sites.
export function projectSnipMessages(messages: Message[]): Message[] {
  return projectSnippedView(messages)
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/cli && bun test src/services/compact/snipProjection.test.ts`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/cli/src/services/compact/snipProjection.ts src/cli/src/services/compact/snipProjection.test.ts
git commit -m "feat(cli): snip projection (stateless boundary-based filtering)" -- src/cli/src/services/compact/snipProjection.ts src/cli/src/services/compact/snipProjection.test.ts
```

---

### Task 18: Snip range math + boundary creation

**Files:**
- Create: `src/cli/src/services/compact/snipRange.ts`
- Test: `src/cli/src/services/compact/snipRange.test.ts`

The load-bearing correctness of snip: resolve `[id:]` anchors, compute the removable range (segment semantics), enforce the rails (protect current turn + latest user message, keep tool_use/tool_result pairs whole, no boundary/system removal), and build the boundary with `removedUuids`.

- [ ] **Step 1: Write the failing test**

```typescript
// src/cli/src/services/compact/snipRange.test.ts
import { expect, test } from 'bun:test'
import { resolveSnipRange, buildSnipBoundary } from './snipRange.js'
import { deriveShortMessageId } from '../../utils/messages.js'

// Build a small transcript. deriveShortMessageId maps uuid -> the [id:] anchor.
function user(uuid: string) { return { type: 'user', uuid, message: { role: 'user', content: `hi [id:${deriveShortMessageId(uuid)}]` } } }
function asst(uuid: string) { return { type: 'assistant', uuid, message: { role: 'assistant', content: 'ok' } } }

const U = (n: number) => `00000000-0000-0000-0000-00000000000${n}`

test('resolves a mid-transcript range by anchor, excludes the latest user turn', () => {
  const msgs = [user(U(1)), asst(U(1)), user(U(2)), asst(U(2)), user(U(3))] as any[]
  const startId = deriveShortMessageId(U(1))
  const endId = deriveShortMessageId(U(2))
  const r = resolveSnipRange(msgs, startId, endId)
  expect('error' in r).toBe(false)
  if ('error' in r) return
  // U(1) user+asst and U(2) user+asst removed; U(3) (latest turn) preserved
  expect(r.removedUuids).toContain(U(1))
  expect(r.removedUuids).toContain(U(2))
  expect(r.removedUuids).not.toContain(U(3))
})

test('rejects a range that includes the latest non-meta user message', () => {
  const msgs = [user(U(1)), asst(U(1)), user(U(3))] as any[]
  const r = resolveSnipRange(msgs, deriveShortMessageId(U(1)), deriveShortMessageId(U(3)))
  expect('error' in r).toBe(true)
})

test('rejects an unresolvable anchor', () => {
  const msgs = [user(U(1))] as any[]
  const r = resolveSnipRange(msgs, 'zzzzzz', 'zzzzzz')
  expect('error' in r).toBe(true)
})

test('buildSnipBoundary carries removedUuids + tokensFreed in the resume shape', () => {
  const b = buildSnipBoundary([U(1), U(2)], 250) as any
  expect(b.type).toBe('system')
  expect(b.subtype).toBe('snip_boundary')
  expect(b.snipMetadata.removedUuids).toEqual([U(1), U(2)])
  expect(b.snipMetadata.tokensFreed).toBe(250)
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cli && bun test src/services/compact/snipRange.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Write the implementation**

```typescript
// src/cli/src/services/compact/snipRange.ts
import { randomUUID } from 'node:crypto'
import type { Message } from '../../types/message.js'
import { deriveShortMessageId } from '../../utils/messages.js'
import { tokenCountWithEstimation } from '../../utils/tokens.js'

type Any = Record<string, unknown>

function uuidOf(m: Message): string | undefined {
  return (m as Any).uuid as string | undefined
}
function isUser(m: Message): boolean {
  return (m as Any).type === 'user'
}
function isMeta(m: Message): boolean {
  return (m as Any).isMeta === true
}
function isSystem(m: Message): boolean {
  return (m as Any).type === 'system'
}

// Index of the message whose uuid derives to `anchor`.
function findAnchor(messages: Message[], anchor: string): number {
  return messages.findIndex(m => {
    const u = uuidOf(m)
    return u !== undefined && deriveShortMessageId(u) === anchor
  })
}

// Index of the last non-meta user message — the current turn's anchor; never
// removable (removing it would drop the live request).
function lastUserIndex(messages: Message[]): number {
  for (let i = messages.length - 1; i >= 0; i--) {
    if (isUser(messages[i]!) && !isMeta(messages[i]!)) return i
  }
  return -1
}

export type SnipRange = { removedUuids: string[]; startIndex: number; endIndex: number }

export function resolveSnipRange(
  messages: Message[],
  startId: string,
  endId: string,
): SnipRange | { error: string } {
  const start = findAnchor(messages, startId)
  const end = findAnchor(messages, endId)
  if (start === -1) return { error: `Could not find message [id:${startId}].` }
  if (end === -1) return { error: `Could not find message [id:${endId}].` }
  if (start > end) return { error: 'start_id must come before end_id.' }

  // Segment end: extend through everything up to (not including) the next
  // non-meta user message after `end`.
  let segEnd = end
  for (let i = end + 1; i < messages.length; i++) {
    if (isUser(messages[i]!) && !isMeta(messages[i]!)) break
    segEnd = i
  }

  const protectedIdx = lastUserIndex(messages)
  if (protectedIdx !== -1 && segEnd >= protectedIdx) {
    return { error: 'Cannot snip the current turn / latest user message.' }
  }

  const removedUuids: string[] = []
  for (let i = start; i <= segEnd; i++) {
    const m = messages[i]!
    if (isSystem(m)) continue // never remove system/boundary messages
    const u = uuidOf(m)
    if (u) removedUuids.push(u)
  }
  if (removedUuids.length === 0) {
    return { error: 'Nothing removable in that range.' }
  }
  return { removedUuids, startIndex: start, endIndex: segEnd }
}

export function buildSnipBoundary(
  removedUuids: string[],
  tokensFreed: number,
  label = 'history snipped',
): Message {
  return {
    type: 'system',
    subtype: 'snip_boundary',
    content: `Snipped ${removedUuids.length} messages (~${tokensFreed} tokens)`,
    isMeta: false,
    level: 'info',
    timestamp: new Date().toISOString(),
    uuid: randomUUID(),
    snipMetadata: { removedUuids, tokensFreed, label },
  } as unknown as Message
}

// Estimate tokens freed by removing a set of messages.
export function estimateTokensFreed(
  messages: Message[],
  removedUuids: string[],
): number {
  const removedSet = new Set(removedUuids)
  const removed = messages.filter(m => {
    const u = uuidOf(m)
    return u && removedSet.has(u)
  })
  return tokenCountWithEstimation(removed as Message[])
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/cli && bun test src/services/compact/snipRange.test.ts`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/cli/src/services/compact/snipRange.ts src/cli/src/services/compact/snipRange.test.ts
git commit -m "feat(cli): snip range math + boundary creation (resume-shape)" -- src/cli/src/services/compact/snipRange.ts src/cli/src/services/compact/snipRange.test.ts
```

---

### Task 19: Snip runtime (`snipCompact.ts`)

**Files:**
- Modify: `src/cli/src/services/compact/snipCompact.ts`
- Test: `src/cli/src/services/compact/snipCompact.test.ts`

The runtime the call sites already consume: `isSnipRuntimeEnabled`, `shouldNudgeForSnips`, `snipCompactIfNeeded`, and the `SNIP_NUDGE_TEXT` export (required by `messages.ts:4152`). Pending Snip tool-uses are marked on a module-level queue (set by SnipTool.call, drained here) — tools can't mutate the store mid-turn.

- [ ] **Step 1: Write the failing test**

```typescript
// src/cli/src/services/compact/snipCompact.test.ts
import { expect, test, beforeEach } from 'bun:test'
import {
  isSnipRuntimeEnabled, shouldNudgeForSnips, snipCompactIfNeeded,
  SNIP_NUDGE_TEXT, _queueSnip, _resetSnipQueueForTest,
} from './snipCompact.js'
import { deriveShortMessageId } from '../../utils/messages.js'

const U = (n: number) => `00000000-0000-0000-0000-00000000000${n}`
const user = (n: number) => ({ type: 'user', uuid: U(n), message: { role: 'user', content: `hi [id:${deriveShortMessageId(U(n))}]` } })
const asst = (n: number) => ({ type: 'assistant', uuid: U(n), message: { role: 'assistant', content: 'x'.repeat(200) } })

beforeEach(() => _resetSnipQueueForTest())

test('runtime enabled unless JARVIS_HISTORY_SNIP=0', () => {
  delete process.env.JARVIS_HISTORY_SNIP
  expect(isSnipRuntimeEnabled()).toBe(true)
  process.env.JARVIS_HISTORY_SNIP = '0'
  expect(isSnipRuntimeEnabled()).toBe(false)
  delete process.env.JARVIS_HISTORY_SNIP
})

test('SNIP_NUDGE_TEXT is a non-empty string', () => {
  expect(typeof SNIP_NUDGE_TEXT).toBe('string')
  expect(SNIP_NUDGE_TEXT.length).toBeGreaterThan(10)
})

test('no queued snip → no-op pass', () => {
  const msgs = [user(1), asst(1), user(2)] as any[]
  const r = snipCompactIfNeeded(msgs)
  expect(r.executed).toBe(false)
  expect(r.messages).toBe(msgs)
})

test('queued snip → inserts a boundary and reports tokensFreed', () => {
  const msgs = [user(1), asst(1), user(2), asst(2), user(3)] as any[]
  _queueSnip(deriveShortMessageId(U(1)), deriveShortMessageId(U(2)))
  const r = snipCompactIfNeeded(msgs)
  expect(r.executed).toBe(true)
  expect(r.tokensFreed).toBeGreaterThan(0)
  expect(r.boundaryMessage).toBeDefined()
  // boundary present in returned list
  expect(r.messages.some((m: any) => m.subtype === 'snip_boundary')).toBe(true)
})

test('invalid queued range → no-op (no boundary)', () => {
  const msgs = [user(1)] as any[]
  _queueSnip('zzzzzz', 'zzzzzz')
  const r = snipCompactIfNeeded(msgs)
  expect(r.executed).toBe(false)
  expect(r.boundaryMessage).toBeUndefined()
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/cli && bun test src/services/compact/snipCompact.test.ts`
Expected: FAIL — stub returns `false`/no-op, no `SNIP_NUDGE_TEXT`/`_queueSnip`.

- [ ] **Step 3: Rewrite the module**

```typescript
// src/cli/src/services/compact/snipCompact.ts
import type { Message } from '../../types/message.js'
import { tokenCountWithEstimation } from '../../utils/tokens.js'
import {
  resolveSnipRange,
  buildSnipBoundary,
  estimateTokensFreed,
} from './snipRange.js'
import { projectSnippedView, isSnipBoundaryMessage } from './snipProjection.js'

export const SNIP_NUDGE_TEXT =
  'Context is growing. If earlier exploration is concluded or superseded, consider using the Snip tool with the [id:] anchors to remove those ranges from context. Never snip anything still needed for the current task.'

const NUDGE_INTERVAL_TOKENS = 10_000

// Env kill-switch. The compile-time feature() gate is the real switch; this
// lets an operator disable the runtime without a rebuild.
export function isSnipRuntimeEnabled(): boolean {
  return process.env.JARVIS_HISTORY_SNIP !== '0'
}

// Pending Snip tool-uses. SnipTool.call() enqueues; snipCompactIfNeeded drains
// at the query boundary (tools must not mutate the store mid-turn).
type PendingSnip = { startId: string; endId: string }
let pendingSnips: PendingSnip[] = []
let lastNudgeTokens = 0

export function _queueSnip(startId: string, endId: string): void {
  pendingSnips.push({ startId, endId })
}
export function _resetSnipQueueForTest(): void {
  pendingSnips = []
  lastNudgeTokens = 0
}

// Nudge once every ~10k tokens of growth; reset on nudge / snip / boundary.
export function shouldNudgeForSnips(messages: Message[]): boolean {
  if (!isSnipRuntimeEnabled()) return false
  const now = tokenCountWithEstimation(messages as Message[])
  if (now - lastNudgeTokens >= NUDGE_INTERVAL_TOKENS) {
    lastNudgeTokens = now
    return true
  }
  return false
}

export type SnipResult = {
  messages: Message[]
  tokensFreed: number
  executed: boolean
  boundaryMessage?: Message
}

export function snipCompactIfNeeded(
  messages: Message[],
  _options?: { force?: boolean },
): SnipResult {
  if (!isSnipRuntimeEnabled() || pendingSnips.length === 0) {
    return { messages, tokensFreed: 0, executed: false }
  }

  const queued = pendingSnips
  pendingSnips = []

  let working = messages
  let totalFreed = 0
  let lastBoundary: Message | undefined
  let anyExecuted = false

  for (const snip of queued) {
    const range = resolveSnipRange(working, snip.startId, snip.endId)
    if ('error' in range) continue // invalid range → skip silently (best-effort)
    const freed = estimateTokensFreed(working, range.removedUuids)
    const boundary = buildSnipBoundary(range.removedUuids, freed)
    // Insert the boundary right after the removed range's end.
    working = [
      ...working.slice(0, range.endIndex + 1),
      boundary,
      ...working.slice(range.endIndex + 1),
    ]
    totalFreed += freed
    lastBoundary = boundary
    anyExecuted = true
    lastNudgeTokens = 0 // reset nudge pacing on a real snip
  }

  if (!anyExecuted) {
    return { messages, tokensFreed: 0, executed: false }
  }

  return {
    messages: working,
    tokensFreed: totalFreed,
    executed: true,
    boundaryMessage: lastBoundary,
  }
}

// Re-export so call sites importing from snipCompact keep working.
export { projectSnippedView, isSnipBoundaryMessage }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/cli && bun test src/services/compact/snipCompact.test.ts`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/cli/src/services/compact/snipCompact.ts src/cli/src/services/compact/snipCompact.test.ts
git commit -m "feat(cli): history-snip runtime (queue, nudge pacing, boundary insert)" -- src/cli/src/services/compact/snipCompact.ts src/cli/src/services/compact/snipCompact.test.ts
```

---

### Task 20: Real `SnipTool` (id-anchored) + boundary message component

**Files:**
- Modify: `src/cli/src/tools/SnipTool/SnipTool.ts`
- Modify: `src/cli/src/components/messages/SnipBoundaryMessage.tsx`

- [ ] **Step 1: Rewrite SnipTool with start_id/end_id**

```typescript
// src/cli/src/tools/SnipTool/SnipTool.ts
import { z } from 'zod/v4'
import { buildTool, type ToolDef } from '../../Tool.js'
import { isSnipRuntimeEnabled, _queueSnip } from '../../services/compact/snipCompact.js'
import { lazySchema } from '../../utils/lazySchema.js'

const inputSchema = lazySchema(() =>
  z.strictObject({
    start_id: z.string().describe('The [id:] anchor of the first message to snip'),
    end_id: z.string().describe('The [id:] anchor of the last message to snip'),
  }),
)
type InputSchema = ReturnType<typeof inputSchema>

const outputSchema = lazySchema(() =>
  z.object({ success: z.boolean(), message: z.string() }),
)
type OutputSchema = ReturnType<typeof outputSchema>
type Output = z.infer<OutputSchema>

export const SnipTool = buildTool({
  name: 'Snip',
  searchHint: 'remove a concluded range of conversation history from context',
  maxResultSizeChars: 10_000,
  get inputSchema(): InputSchema {
    return inputSchema()
  },
  get outputSchema(): OutputSchema {
    return outputSchema()
  },
  isEnabled() {
    return true
  },
  isReadOnly() {
    return true
  },
  isConcurrencySafe() {
    return true
  },
  async description() {
    return 'Remove a concluded/superseded range of conversation history from context, addressed by [id:] anchors. The removal is applied at the next turn boundary.'
  },
  async prompt() {
    return 'Use Snip to drop ranges of history that are concluded or superseded, freeing context. Address the range with the [id:] anchors shown on user messages. Never snip content still needed for the current task; you cannot snip the current turn.'
  },
  renderToolUseMessage() {
    return null
  },
  async call({ start_id, end_id }) {
    if (!isSnipRuntimeEnabled()) {
      return {
        data: {
          success: false,
          message: 'History snip runtime is disabled (JARVIS_HISTORY_SNIP=0).',
        },
      }
    }
    // Queue for the next query boundary; the runtime validates + applies there.
    _queueSnip(start_id, end_id)
    return {
      data: {
        success: true,
        message: `Queued snip of [id:${start_id}]…[id:${end_id}]; it will be applied on the next turn (invalid ranges are ignored).`,
      },
    }
  },
  mapToolResultToToolResultBlockParam(output, toolUseID) {
    return {
      tool_use_id: toolUseID,
      type: 'tool_result',
      content: output.message,
      is_error: !output.success,
    }
  },
} satisfies ToolDef<InputSchema, Output>)
```

- [ ] **Step 2: Boundary message component**

```tsx
// src/cli/src/components/messages/SnipBoundaryMessage.tsx
import { Text } from 'ink'
import * as React from 'react'

type Props = {
  message: { snipMetadata?: { removedUuids?: string[]; tokensFreed?: number } }
}

export function SnipBoundaryMessage({ message }: Props): React.ReactNode {
  const n = message.snipMetadata?.removedUuids?.length ?? 0
  const tok = message.snipMetadata?.tokensFreed ?? 0
  return (
    <Text dimColor>
      ✂ {n} message{n === 1 ? '' : 's'} snipped{tok ? ` (~${tok} tokens freed)` : ''}
    </Text>
  )
}
```

- [ ] **Step 3: Parse check both**

Run: `cd src/cli && bun build src/tools/SnipTool/SnipTool.ts --no-bundle && bun build src/components/messages/SnipBoundaryMessage.tsx --no-bundle`
Expected: both compile.

- [ ] **Step 4: Verify Message.tsx renders the boundary**

Read `src/cli/src/components/Message.tsx:249-269` — it already lazy-requires `isSnipBoundaryMessage` + `SnipBoundaryMessage` and passes `message={message}`. Confirm the prop name matches (`message`). No edit expected; if the prop differs, align the component.

- [ ] **Step 5: Commit**

```bash
git add src/cli/src/tools/SnipTool/SnipTool.ts src/cli/src/components/messages/SnipBoundaryMessage.tsx
git commit -m "feat(cli): id-anchored Snip tool + boundary message render" -- src/cli/src/tools/SnipTool/SnipTool.ts src/cli/src/components/messages/SnipBoundaryMessage.tsx
```

---

### Task 21: Enable `HISTORY_SNIP` + smoke

**Files:**
- Modify: `src/cli/scripts/start.sh`

- [ ] **Step 1: Full suite**

Run: `cd src/cli && bun test`
Expected: everything green (Part 1 + Part 2 + existing 201). Fix red before flipping.

- [ ] **Step 2: Add the flag**

Add `--feature=HISTORY_SNIP` to the feature list in `src/cli/scripts/start.sh`.

- [ ] **Step 3: Boot check**

Run: `cd /home/ulrich/Documents/Projects/jarvis && bin/jarvis -p "say OK"`
Expected: returns OK, exits 0. If it hangs, bisect (remove flag, confirm boot, fix the throwing require — likely a wrong import in snipCompact/snipProjection/SnipTool).

- [ ] **Step 4: Snip smoke**

Run a short interactive session, then verify the `[id:]` anchors appear on user messages and asking the model to "snip the earliest exchange" produces a `✂ … snipped` boundary line:

```bash
cd /home/ulrich/Documents/Projects/jarvis && bin/jarvis -p $'Say A.\nSay B.\nNow snip the first exchange using its [id:] anchor, then say C.'
```

Expected: a boundary appears; the run still returns cleanly. (Non-interactive `-p` keeps full history in one turn; the primary assertion is "no crash + tool invoked + boundary created". Deeper multi-turn projection is exercised by the unit tests.)

- [ ] **Step 5: Commit**

```bash
git add src/cli/scripts/start.sh
git commit -m "feat(cli): enable HISTORY_SNIP feature flag" -- src/cli/scripts/start.sh
```

---

### Task 22: Final regression pass

- [ ] **Step 1: Full suite**

Run: `cd src/cli && bun test`
Expected: green.

- [ ] **Step 2: Boot with BOTH flags live**

Run: `cd /home/ulrich/Documents/Projects/jarvis && bin/jarvis -p "say OK"`
Expected: OK, exit 0.

- [ ] **Step 3: Confirm no sibling tree touched**

Run: `git status --porcelain | grep -v '^.. src/cli/\|^.. docs/superpowers/'`
Expected: only pre-existing unrelated dirty files from parallel sessions (voice-agent/web/desktop) — NONE newly modified by this plan. If this plan touched anything outside `src/cli/` or `docs/superpowers/`, STOP and surface it (rule 6: no silent scope creep).

- [ ] **Step 4: End-of-task summary**

Write the rule-7 summary: CHANGED (files + why), NOT CHANGED (voice-agent/web/desktop/android — untouched), VERIFY (bun test result + boot check).

---

## Self-Review notes (author)

- **Spec coverage:** meta parser (T1), progress types (T2), journal/resume (T3), pipeline/parallel + caps (T4/T5), agent()/schema/skip (T6), vm + determinism (T7), named loader (T8), runner (T9), runAgent dispatch (T10), task state + skip/kill (T11), verbatim prompt (T12), tool + validate + permissions + background launch (T13), /workflows + slash cmds + both dialogs (T14), progress batching (T15), enable + smoke (T16); snip projection (T17), range math + boundary resume-shape (T18), runtime + nudge + SNIP_NUDGE_TEXT (T19), id-anchored tool + boundary render (T20), enable + smoke (T21), regression (T22). All spec sections map to a task.
- **Retry semantics:** spec says live mid-run retry is out of scope; T11 keeps `retryWorkflowAgent` a no-op and T14's dialog shows the resume hint — consistent.
- **Type consistency:** `SdkWorkflowProgress` (T2) used by T6/T9/T11/T14/T15; `resolveSnipRange`/`buildSnipBoundary` (T18) used by T19; `_queueSnip` (T19) used by T20; `parseWorkflowMeta`/`checkDeterminism` (T1) used by T8/T13. Names consistent across tasks.
- **Known integration risk (flagged for the executor):** T10 `dispatch.ts` uses `as never` placeholders for the exact `runAgent` params + finalize accessors — the executor MUST replace them with the real shapes read from `AgentTool.tsx:603` + `agentToolUtils.ts:276` + `SyntheticOutputTool.ts`. This is the one module without a unit test; the T16 smoke is its gate.
