/**
 * POST /api/evolution/[id]/dismiss — drop a queued intent before it's built.
 *
 * The queued-stage counterpart to reject: runs the host-side
 * `jarvis-automod dismiss <id>`, which removes the intent from
 * ~/.jarvis/auto-mods/queue.jsonl (under the same lock the spawner uses) so it
 * never gets built. Nothing is deployed. Same-origin from the logged-in page
 * (proxy.ts gates it).
 *
 * Injection-safe: the id is validated against a strict pattern and passed via
 * execFile args (no shell). No user-controlled positionals beyond the id.
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
    return Response.json({ ok: false, detail: 'invalid intent id' }, { status: 400 })
  }
  try {
    const { stdout } = await execFileP(AUTOMOD_BIN, ['dismiss', id], {
      timeout: 30_000,
      maxBuffer: 1024 * 1024,
    })
    return Response.json({ ok: true, detail: stdout.trim() })
  } catch (e) {
    const err = e as { stderr?: string; stdout?: string; message?: string }
    return Response.json(
      { ok: false, detail: (err.stderr || err.stdout || err.message || 'dismiss failed').trim() },
      { status: 502 },
    )
  }
}
