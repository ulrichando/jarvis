// In-memory confirmation queue. Not durable across restarts — intentional for Plan 4.

export type ConfirmationRequest = {
  id: string;
  tool: string;
  input: unknown;
  reason: string;
  promptText: string;
  createdAt: number;
};

export type Decision = "allow" | "deny";

type PendingEntry = {
  request: ConfirmationRequest;
  resolve: (decision: Decision) => void;
  reject: (err: Error) => void;
  timer: ReturnType<typeof setTimeout>;
};

const DEFAULT_TIMEOUT_MS = 5 * 60 * 1000; // 5 minutes

export class ConfirmationQueue {
  private pending = new Map<string, PendingEntry>();
  private counter = 0;

  constructor(private readonly timeoutMs: number = DEFAULT_TIMEOUT_MS) {}

  /** Open a new confirmation request; returns its id and a promise that resolves when a client calls `resolve`. */
  open(opts: { tool: string; input: unknown; reason: string; promptText: string }): {
    id: string;
    wait: Promise<Decision>;
  } {
    const id = `c_${++this.counter}_${Date.now().toString(36)}`;
    const request: ConfirmationRequest = {
      id,
      tool: opts.tool,
      input: opts.input,
      reason: opts.reason,
      promptText: opts.promptText,
      createdAt: Date.now(),
    };
    let resolveOuter: (d: Decision) => void;
    let rejectOuter: (e: Error) => void;
    const wait = new Promise<Decision>((res, rej) => {
      resolveOuter = res;
      rejectOuter = rej;
    });
    const timer = setTimeout(() => {
      const entry = this.pending.get(id);
      if (!entry) return;
      this.pending.delete(id);
      entry.reject(new Error(`confirmation ${id} timed out after ${this.timeoutMs}ms`));
    }, this.timeoutMs);
    this.pending.set(id, { request, resolve: resolveOuter!, reject: rejectOuter!, timer });
    return { id, wait };
  }

  /** Resolve a pending confirmation. Returns true if resolved, false if unknown or already resolved. */
  resolve(id: string, decision: Decision): boolean {
    const entry = this.pending.get(id);
    if (!entry) return false;
    this.pending.delete(id);
    clearTimeout(entry.timer);
    entry.resolve(decision);
    return true;
  }

  /** Read a pending request's metadata without resolving it. */
  get(id: string): ConfirmationRequest | undefined {
    return this.pending.get(id)?.request;
  }

  /** List all pending requests. */
  list(): ConfirmationRequest[] {
    return Array.from(this.pending.values()).map((e) => e.request);
  }

  /** Shut down: reject all pending with an error. For tests and graceful shutdown. */
  shutdown(): void {
    for (const [id, entry] of this.pending) {
      clearTimeout(entry.timer);
      entry.reject(new Error(`confirmation queue shutting down; ${id} abandoned`));
    }
    this.pending.clear();
  }
}
