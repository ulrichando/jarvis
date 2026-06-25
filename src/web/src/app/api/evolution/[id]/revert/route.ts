/**
 * POST /api/evolution/[id]/revert — roll back a deployed self-evolution change.
 *
 * Runs the host-side `jarvis-automod revert <id>`, which resets to the recorded
 * rollback point (or git-reverts the merge) and restarts the agent. The single
 * actuator for the rollback-history "Roll back" button. Same-origin (proxy.ts
 * gates it). Injection-safe: id validated + passed via execFile args (no shell).
 */
import { execFile } from 'child_process'
import path from 'path'
import { promisify } from 'util'

const execFileP = promisify(execFile)

export const dynamic = 'force-dynamic'
export const runtime = 'nodejs'

const AUTOMOD_BIN = path.resolve(process.cwd(), '..', '..', 'bin', 'jarvis-automod')
const ID_RE = /^automod-[A-Za-z0-9._-]+$/

export async function POST(
  _req: Request,
  ctx: { params: Promise<{ id: string }> },
): Promise<Response> {
  const { id } = await ctx.params
  if (!ID_RE.test(id)) {
    return Response.json({ ok: false, detail: 'invalid id' }, { status: 400 })
  }
  try {
    const { stdout } = await execFileP(AUTOMOD_BIN, ['revert', id], {
      timeout: 120_000,
      maxBuffer: 1024 * 1024,
    })
    return Response.json({ ok: true, detail: stdout.trim() })
  } catch (e) {
    const err = e as { stderr?: string; stdout?: string; message?: string }
    return Response.json(
      { ok: false, detail: (err.stderr || err.stdout || err.message || 'revert failed').trim() },
      { status: 502 },
    )
  }
}
