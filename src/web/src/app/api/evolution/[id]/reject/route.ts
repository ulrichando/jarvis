/**
 * POST /api/evolution/[id]/reject — reject a pending self-evolution proposal.
 *
 * The counterpart to approve: runs the host-side `jarvis-automod reject <id>
 * <reason>`, which deletes the proposal branch and marks the artifact rejected.
 * Nothing is deployed. Same-origin from the logged-in page (proxy.ts gates it).
 *
 * Injection-safe: the id is validated against a strict pattern and the id +
 * reason are passed via execFile args (no shell).
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
  req: Request,
  ctx: { params: Promise<{ id: string }> },
): Promise<Response> {
  const { id } = await ctx.params
  if (!ID_RE.test(id)) {
    return Response.json({ ok: false, detail: 'invalid proposal id' }, { status: 400 })
  }
  let reason = 'Rejected from the evolution review UI'
  try {
    const body = (await req.json()) as { reason?: unknown }
    if (typeof body?.reason === 'string' && body.reason.trim()) {
      // Strip leading dashes so the reason can never be parsed as a CLI flag
      // (argv flag-smuggling defense — the CLI takes it positionally today, but
      // this stays safe if it ever migrates to argparse). Re-default if emptied.
      const cleaned = body.reason.trim().replace(/^-+\s*/, '').trim().slice(0, 500)
      if (cleaned) reason = cleaned
    }
  } catch {
    /* no/!JSON body — use the default reason */
  }
  try {
    const { stdout } = await execFileP(AUTOMOD_BIN, ['reject', id, reason], {
      timeout: 60_000,
      maxBuffer: 1024 * 1024,
    })
    return Response.json({ ok: true, detail: stdout.trim() })
  } catch (e) {
    const err = e as { stderr?: string; stdout?: string; message?: string }
    return Response.json(
      { ok: false, detail: (err.stderr || err.stdout || err.message || 'reject failed').trim() },
      { status: 502 },
    )
  }
}
