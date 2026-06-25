/**
 * POST /api/evolution/pause — pause/resume the autonomous evolution cycle.
 *
 * Toggles the pause flag (~/.jarvis/auto-mods/.evolution-paused) that the build
 * cycle + spawner check between builds. Body: {paused: boolean}. Universal-signal
 * file pattern (mirrors ~/.jarvis/.silent-mode) — pausing stops new builds
 * cleanly without killing an in-flight one. Same-origin (proxy.ts gates it).
 */
import { promises as fs } from 'fs'
import os from 'os'
import path from 'path'

export const dynamic = 'force-dynamic'
export const runtime = 'nodejs'

// Must match pipeline/automod/_state.py::pause_flag_path().
const FLAG = path.join(os.homedir(), '.jarvis', 'auto-mods', '.evolution-paused')

export async function POST(req: Request): Promise<Response> {
  let paused = true
  try {
    const body = (await req.json()) as { paused?: unknown }
    paused = !!body?.paused
  } catch {
    /* default: pause */
  }
  try {
    if (paused) {
      await fs.mkdir(path.dirname(FLAG), { recursive: true })
      await fs.writeFile(FLAG, 'paused\n', 'utf-8')
    } else {
      await fs.rm(FLAG, { force: true })
    }
    return Response.json({ ok: true, paused })
  } catch (e) {
    return Response.json({ ok: false, detail: String((e as Error)?.message ?? e) }, { status: 500 })
  }
}
