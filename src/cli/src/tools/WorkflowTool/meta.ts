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

  // Evaluate the literal in an empty (null-proto) context with codegen denied
  // and a hard timeout. A pure data literal evaluates; any identifier ref,
  // call, or spread throws (no globals) -> reject as "not a pure literal".
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
