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
