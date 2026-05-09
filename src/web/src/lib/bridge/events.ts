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
