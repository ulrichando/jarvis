/**
 * POST /api/evolution/[id]/review — run the 3-lens review council on a proposal.
 *
 * Runs the host-side `jarvis-evolution-review <id>`: correctness / security /
 * regression LLM reviews of the diff, fused into one recommendation and written
 * to ~/.jarvis/auto-mods/<id>.review.json (which GET /api/evolution surfaces).
 * ADVISORY ONLY — it never deploys; the human still approves. Synchronous (three
 * LLM calls), so a generous timeout. Same-origin, proxy-gated. Injection-safe:
 * the id is validated and passed via execFile args (no shell).
 */
import { execFile } from 'child_process'
import path from 'path'
import { promisify } from 'util'

const execFileP = promisify(execFile)

export const dynamic = 'force-dynamic'
export const runtime = 'nodejs'

const REVIEW_BIN = path.resolve(process.cwd(), '..', '..', 'bin', 'jarvis-evolution-review')
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
    const { stdout } = await execFileP(REVIEW_BIN, [id], {
      timeout: 180_000,
      maxBuffer: 4 * 1024 * 1024,
    })
    let review: unknown = null
    try {
      review = JSON.parse(stdout.trim())
    } catch {
      /* council prints JSON on success; non-JSON falls through to ok:true, review:null */
    }
    return Response.json({ ok: true, review })
  } catch (e) {
    const err = e as { stderr?: string; stdout?: string; message?: string }
    return Response.json(
      { ok: false, detail: (err.stderr || err.stdout || err.message || 'review failed').trim() },
      { status: 502 },
    )
  }
}
