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
  GuardedDate.parse = Date.parse
  GuardedDate.UTC = Date.UTC
  Object.defineProperty(GuardedDate, 'now', {
    value: () => {
      throw new Error('Date.now() is unavailable in workflow scripts (breaks resume).')
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
// read `result` off the context.
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
