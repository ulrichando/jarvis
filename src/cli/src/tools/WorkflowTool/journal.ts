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
  // Addressed by CALL INDEX (agent() invocation order), NOT completion order.
  // Parallel agents finish out of order, so a push()-based journal recorded
  // results in completion order while lookup() reads by call index — on resume
  // that replayed each cached result to the WRONG call. Index-addressing keeps
  // record and lookup aligned. Holes (skipped/failed calls that never recorded)
  // serialize as explicit nulls so the on-disk index alignment survives.
  private recorded: (JournalEntry | null)[] = []
  private prior: (JournalEntry | null)[] = []
  // Once a resume call diverges, every later call runs live even if its hash
  // coincidentally matches a prior entry. This is the prefix invariant.
  private diverged = false

  static fromEntries(entries: (JournalEntry | null)[]): WorkflowJournal {
    const j = new WorkflowJournal()
    j.prior = entries
    return j
  }

  entries(): (JournalEntry | null)[] {
    // Dense array over 0..maxRecordedIndex so holes serialize as `null` lines
    // and the reloaded journal stays index-aligned (a sparse array would drop
    // holes on .map and shift every later entry).
    return Array.from(
      { length: this.recorded.length },
      (_, i) => this.recorded[i] ?? null,
    )
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
    // Re-record the hit at its call index so entries() reproduces the FULL
    // journal — otherwise a resumed run persists only the newly-live agents and
    // the cached prefix is lost, degrading journal.jsonl on every resume.
    this.recorded[index] = prior
    return { hit: true, result: prior.result }
  }

  record(index: number, prompt: string, opts: unknown, result: unknown): void {
    this.recorded[index] = { hash: hashCall(prompt, opts), result }
  }
}
