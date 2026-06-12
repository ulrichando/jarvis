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
