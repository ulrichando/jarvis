import { EventEmitter } from 'node:events'

const bus = new EventEmitter()
bus.setMaxListeners(0) // unbounded — we listen per-poll

function eventName(envId: string): string {
  return `work-available:${envId}`
}

export function emitWorkAvailable(envId: string): void {
  bus.emit(eventName(envId))
}

/**
 * Wait until either work-available is emitted for this env, or the
 * timeout elapses. Returns true on event, false on timeout. Always
 * unsubscribes the listener so we don't leak.
 */
export function waitForWork(envId: string, timeoutMs: number): Promise<boolean> {
  return new Promise(resolve => {
    let done = false
    const cleanup = (val: boolean) => {
      if (done) return
      done = true
      clearTimeout(timer)
      bus.off(eventName(envId), onEvent)
      resolve(val)
    }
    const onEvent = () => cleanup(true)
    bus.once(eventName(envId), onEvent)
    const timer = setTimeout(() => cleanup(false), timeoutMs)
  })
}

function inboundEventName(sessionId: string): string {
  return `inbound:${sessionId}`
}

/** Wake the session's SSE stream — a web client queued an inbound message. */
export function emitInbound(sessionId: string): void {
  bus.emit(inboundEventName(sessionId))
}

/**
 * Wait until an inbound message is emitted for this session, or the timeout
 * elapses. Same contract as waitForWork. The SSE loop ALSO re-reads the
 * queue after every timeout, so direct DB inserts (tests, admin tooling)
 * are picked up within one keepalive interval even without an emit.
 */
export function waitForInbound(
  sessionId: string,
  timeoutMs: number,
): Promise<boolean> {
  return new Promise(resolve => {
    let done = false
    const cleanup = (val: boolean) => {
      if (done) return
      done = true
      clearTimeout(timer)
      bus.off(inboundEventName(sessionId), onEvent)
      resolve(val)
    }
    const onEvent = () => cleanup(true)
    bus.once(inboundEventName(sessionId), onEvent)
    const timer = setTimeout(() => cleanup(false), timeoutMs)
  })
}

// ── Live-typing snapshots ───────────────────────────────────────────────────
// The worker's ephemeral stream_events carry FULL-so-far text per content
// block (the CLI coalesces deltas into self-contained snapshots — see
// ccrClient.ts accumulateStreamEvents). They are deliberately not persisted;
// this in-memory map holds the latest snapshot per block so the /code events
// poll can show the reply as it streams. Cleared when the final assistant
// message (or result) is ingested.
const liveText = new Map<string, Map<number, string>>()

export function setLiveText(
  sessionId: string,
  blockIndex: number,
  text: string,
): void {
  let blocks = liveText.get(sessionId)
  if (!blocks) {
    blocks = new Map()
    liveText.set(sessionId, blocks)
  }
  blocks.set(blockIndex, text)
}

export function clearLiveText(sessionId: string): void {
  liveText.delete(sessionId)
}

/** Joined in-flight text for the session, or null when nothing is streaming. */
export function getLiveText(sessionId: string): string | null {
  const blocks = liveText.get(sessionId)
  if (!blocks || blocks.size === 0) return null
  const joined = [...blocks.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([, t]) => t)
    .join('')
    .trim()
  return joined || null
}
