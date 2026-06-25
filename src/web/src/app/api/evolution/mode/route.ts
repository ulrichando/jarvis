/**
 * POST /api/evolution/mode — switch JARVIS evolution between manual and auto.
 *
 * AUTO is represented by ~/.jarvis/auto-mods/.evolution-auto. The Python
 * scheduler reads the same flag, so the web UI and timer share one source of
 * truth. Manual mode is the default when the file is absent.
 */
import { promises as fs } from 'fs'
import os from 'os'
import path from 'path'

export const dynamic = 'force-dynamic'
export const runtime = 'nodejs'

const FLAG = path.join(os.homedir(), '.jarvis', 'auto-mods', '.evolution-auto')

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
    } else {
      await fs.rm(FLAG, { force: true })
    }
    return Response.json({ ok: true, mode, autoMode: mode === 'auto' })
  } catch (e) {
    return Response.json({ ok: false, detail: String((e as Error)?.message ?? e) }, { status: 500 })
  }
}
