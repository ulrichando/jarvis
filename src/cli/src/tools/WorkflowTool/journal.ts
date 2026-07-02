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
