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

// The real dispatcher (later task) runs runAgent and reduces its message
// stream. Under test it's a stub. Returns exactly one of: text|structured|skipped.
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
