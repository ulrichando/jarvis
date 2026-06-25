/**
 * POST /api/evolution/cycle — start the autonomous build cycle.
 *
 * Spawns the host-side `jarvis-evolution-cycle` DETACHED: it assesses, queues
 * improvements, and builds them ONE AT A TIME with learn-and-retry on failure.
 * Long-running (each build spawns a coding agent), so this returns immediately;
 * the page polls and proposals appear in Review / Failed as they finish. The
 * cycle honors the pause flag and never deploys (passing builds await review).
 * Same-origin from the logged-in page (proxy.ts gates it).
 */
import { spawn } from 'child_process'
import path from 'path'

export const dynamic = 'force-dynamic'
export const runtime = 'nodejs'

const CYCLE_BIN = path.resolve(process.cwd(), '..', '..', 'bin', 'jarvis-evolution-cycle')

export async function POST(req: Request): Promise<Response> {
  let args: string[] = []
  try {
    const body = (await req.json()) as { maxIntents?: unknown }
    if (typeof body?.maxIntents === 'number' && Number.isInteger(body.maxIntents) && body.maxIntents > 0) {
      args = [String(body.maxIntents)]
    }
  } catch {
    /* no body */
  }
  try {
    const child = spawn(CYCLE_BIN, args, {
      detached: true,
      stdio: 'ignore',
      env: { ...process.env, JARVIS_AUTOMOD_SPAWN_LIVE: '1' },
    })
    child.unref()
    return Response.json(
      { ok: true, detail: 'Build cycle started — builds run one at a time; watch Review / Failed.' },
      { status: 202 },
    )
  } catch (e) {
    const err = e as { message?: string }
    return Response.json({ ok: false, detail: (err.message || 'failed to start cycle').trim() }, { status: 502 })
  }
}
