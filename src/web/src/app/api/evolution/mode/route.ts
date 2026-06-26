/**
 * POST /api/evolution/mode — switch JARVIS evolution between manual and auto.
 *
 * AUTO is represented by ~/.jarvis/auto-mods/.evolution-auto. The Python
 * scheduler reads the same flag, so the web UI and timer share one source of
 * truth. Manual mode is the default when the file is absent. Switching TO auto
 * also kicks off a build cycle immediately, so enabling Auto does something now
 * instead of only at the once-a-day nightly.
 */
import { spawn } from 'child_process'
import { promises as fs } from 'fs'
import os from 'os'
import path from 'path'

export const dynamic = 'force-dynamic'
export const runtime = 'nodejs'

const FLAG = path.join(os.homedir(), '.jarvis', 'auto-mods', '.evolution-auto')
const CYCLE_BIN = path.resolve(process.cwd(), '..', '..', 'bin', 'jarvis-evolution-cycle')

function parseMode(value: unknown): 'manual' | 'auto' | null {
  if (value === 'manual' || value === 'auto') return value
  return null
}

export async function POST(req: Request): Promise<Response> {
  let mode: 'manual' | 'auto' | null = null
  try {
    const body = (await req.json()) as { mode?: unknown; auto?: unknown }
    mode = parseMode(body?.mode)
    if (!mode && typeof body?.auto === 'boolean') {
      mode = body.auto ? 'auto' : 'manual'
    }
  } catch {
    /* invalid body handled below */
  }
  if (!mode) {
    return Response.json({ ok: false, detail: 'mode must be manual or auto' }, { status: 400 })
  }
  try {
    if (mode === 'auto') {
      await fs.mkdir(path.dirname(FLAG), { recursive: true })
      await fs.writeFile(FLAG, 'auto\n', 'utf-8')
      // Build now, not only at the nightly. Detached + best-effort; the cycle's
      // own marker prevents a duplicate run, and the daily cap + retry
      // circuit-breaker bound how much it does.
      try {
        spawn(CYCLE_BIN, [], { detached: true, stdio: 'ignore' }).unref()
      } catch {
        /* flag is set regardless; the nightly still picks it up */
      }
    } else {
      await fs.rm(FLAG, { force: true })
    }
    return Response.json({ ok: true, mode, autoMode: mode === 'auto' })
  } catch (e) {
    return Response.json({ ok: false, detail: String((e as Error)?.message ?? e) }, { status: 500 })
  }
}
