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
