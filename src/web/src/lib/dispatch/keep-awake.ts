import { spawn, type ChildProcess } from 'node:child_process'

// Keep-awake enforcement for Dispatch sessions with keep_awake=true. The web
// server (jarvis-web.service) runs ON the user's desktop — same box as the
// /code worker — so this process can hold a real sleep inhibitor via
// `systemd-inhibit` while a dispatched task runs. A VPS-deployed web app can't
// inhibit the user's desktop; there (and on non-Linux / missing
// systemd-inhibit) acquire silently no-ops.
//
// Lifecycle: acquire on dispatch-create (keep_awake=true) → release on the
// task's done push (fire.ts 'idle'), interrupt, archive, delete, or the
// keep_awake toggle flipping off. Safety cap: the inhibitor wraps
// `sleep 21600` (6h), so an orphaned lock (web server killed mid-task)
// self-releases instead of blocking sleep forever.

const CAP_SECONDS = 21600 // 6h — bounds an orphaned inhibitor

const inhibitors = new Map<string, ChildProcess>()
let warned = false

function warnOnce(err: unknown): void {
  if (warned) return
  warned = true
  console.warn(
    '[dispatch keep-awake] systemd-inhibit unavailable — keep-awake no-ops on this host:',
    err instanceof Error ? err.message : String(err),
  )
}

/** Hold a sleep inhibitor for this session. Idempotent; never throws. */
export function acquireKeepAwake(sessionId: string): void {
  if (inhibitors.has(sessionId)) return
  try {
    const child = spawn(
      'systemd-inhibit',
      [
        '--what=idle:sleep',
        '--mode=block',
        '--who=Jarvis Dispatch',
        '--why=dispatch task running',
        'sleep',
        String(CAP_SECONDS),
      ],
      { stdio: 'ignore' },
    )
    child.on('error', (err) => {
      inhibitors.delete(sessionId)
      warnOnce(err)
    })
    child.on('exit', () => {
      inhibitors.delete(sessionId) // released, killed, or cap elapsed
    })
    child.unref() // never keep the server process alive for the lock
    inhibitors.set(sessionId, child)
  } catch (err) {
    warnOnce(err)
  }
}

/** Drop the session's inhibitor. No-op when none is held; never throws. */
export function releaseKeepAwake(sessionId: string): void {
  const child = inhibitors.get(sessionId)
  if (!child) return
  inhibitors.delete(sessionId)
  try {
    child.kill('SIGTERM') // systemd-inhibit exits → lock released
  } catch {
    /* already gone */
  }
}
