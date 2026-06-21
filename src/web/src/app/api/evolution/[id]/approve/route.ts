/**
 * POST /api/evolution/[id]/approve — approve a self-evolution proposal + deploy.
 *
 * The single actuator for the "I approve, then deploy" step: runs the host-side
 * `jarvis-automod deploy <id>`, which ff-merges the proposal, records a rollback
 * point, restarts the agent into the new code, and arms the external watchdog —
 * which auto-rolls-back if the new code is unhealthy. Same-origin from the
 * logged-in page (proxy.ts gates it).
 *
 * Injection-safe: the id is validated against a strict pattern and passed via
 * execFile args (no shell).
 */
import { execFile } from 'child_process'
import path from 'path'
import { promisify } from 'util'

const execFileP = promisify(execFile)

export const dynamic = 'force-dynamic'
export const runtime = 'nodejs'

// The web app runs from src/web → repo root is two levels up.
const AUTOMOD_BIN = path.resolve(process.cwd(), '..', '..', 'bin', 'jarvis-automod')
const ID_RE = /^automod-[A-Za-z0-9._-]+$/

export async function POST(
  _req: Request,
  ctx: { params: Promise<{ id: string }> },
): Promise<Response> {
  const { id } = await ctx.params
  if (!ID_RE.test(id)) {
    return Response.json({ ok: false, detail: 'invalid proposal id' }, { status: 400 })
  }
  try {
    const { stdout } = await execFileP(AUTOMOD_BIN, ['deploy', id], {
      timeout: 120_000,
      maxBuffer: 1024 * 1024,
    })
    return Response.json({ ok: true, detail: stdout.trim() })
  } catch (e) {
    const err = e as { stderr?: string; stdout?: string; message?: string }
    return Response.json(
      { ok: false, detail: (err.stderr || err.stdout || err.message || 'deploy failed').trim() },
      { status: 502 },
    )
  }
}
