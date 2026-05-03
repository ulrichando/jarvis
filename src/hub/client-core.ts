// JARVIS event hub — runtime-agnostic core.
//
// Everything in this file works under Bun, Node.js, or the Next.js
// dev/prod server unchanged. The SQLite-driver-dependent reads
// (readRecent / readSession) live in the per-runtime client.ts that
// extends HubClientBase below.
//
// IMPORTANT: this file MUST be byte-identical between
//   - src/hub/client-core.ts
//   - src/web/src/lib/hub/client-core.ts
// because Next.js Turbopack refuses to import code outside `src/web/`
// (treats it as "Invalid symlink" / "outside [project]/"), so a single
// shared file isn't possible. A drift detector keeps them in sync —
// run `bun run sync-hub-core` (or invoke the test in voice-agent's
// pytest suite) to enforce.

import Redis from 'ioredis'
import { randomUUID } from 'crypto'
import { homedir } from 'os'
import { join } from 'path'

export const EVENTS_STREAM = 'events:conversation'
export const MEMORY_EVENTS_STREAM = 'events:memory'
export const OFFLINE_MAX = 100

export type Source = 'voice' | 'web' | 'cli' | 'phone' | 'extension'

export type EventType =
  | 'conversation.message.created'
  | 'conversation.session.started'
  | 'conversation.session.ended'
  | 'memory.value.upserted'
  | 'memory.value.removed'

export interface EventPayload {
  role?: 'user' | 'assistant'
  text?: string
  title?: string | null
  tool_calls?: unknown
  // memory.value.upserted / .removed
  memory_id?: string
  content?: string
  category?: string
  source_session_id?: string | null
}

export interface HubEvent {
  source: Source
  source_event_id: string
  type: EventType
  session_id: string
  source_ts: number
  payload: EventPayload
}

/** Default state.db path. Overridable via JARVIS_HUB_DB. */
export function stateDbPathDefault(): string {
  return process.env.JARVIS_HUB_DB
    ?? join(homedir(), '.jarvis', 'hub', 'state.db')
}

/**
 * Runtime-agnostic publisher + offline buffer.
 *
 * Per-runtime subclasses add SQLite reads via static methods; the
 * publish/flush path here is identical across Bun, Node, and Next.js.
 */
export class HubClientBase {
  protected offline: { stream: string; data: string }[] = []

  constructor(
    protected readonly redis: Redis,
    protected readonly source: Source,
  ) {}

  /**
   * Construct a client from JARVIS_HUB_URL (or default redis://127.0.0.1:6379).
   * Subclasses should override to return their concrete class type if needed.
   */
  static fromEnvBase(source: Source): HubClientBase {
    const url = process.env.JARVIS_HUB_URL ?? 'redis://127.0.0.1:6379'
    return new HubClientBase(new Redis(url), source)
  }

  async publish(
    type: EventType,
    sessionId: string,
    payload: EventPayload = {},
    opts: { stream?: string } = {},
  ): Promise<string> {
    const stream = opts.stream ?? EVENTS_STREAM
    const eid = randomUUID().replace(/-/g, '')
    const evt: HubEvent = {
      source: this.source,
      source_event_id: eid,
      type,
      session_id: sessionId,
      source_ts: Date.now(),
      payload,
    }
    const record = { stream, data: JSON.stringify(evt) }
    try {
      await this.redis.xadd(stream, '*', 'data', record.data)
    } catch {
      this.offline.push(record)
      if (this.offline.length > OFFLINE_MAX) this.offline.shift()
    }
    return eid
  }

  /** Replay buffered events. Stops on first failure; rest stay queued. */
  async flushOfflineQueue(): Promise<number> {
    let flushed = 0
    while (this.offline.length > 0) {
      const r = this.offline[0]
      try {
        await this.redis.xadd(r.stream, '*', 'data', r.data)
      } catch {
        break
      }
      this.offline.shift()
      flushed++
    }
    return flushed
  }

  async close(): Promise<void> {
    await this.redis.quit()
  }
}
