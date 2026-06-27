/**
 * POST /api/evolution/review-all — run the 3-lens review council on EVERY
 * pending proposal, in parallel (bounded by JARVIS_AUTOMOD_REVIEW_CONCURRENCY,
 * default 4). Lets the user review the whole backlog in one action instead of
 * one-at-a-time through the UI's single-action gate.
 *
 * Runs host-side `jarvis-evolution-review --all`. Synchronous (several LLM calls
 * across proposals), so a generous timeout. ADVISORY ONLY — never deploys.
 * Same-origin, proxy-gated. No args → no injection surface.
 */
import { execFile } from 'child_process'
import path from 'path'
import { promisify } from 'util'

const execFileP = promisify(execFile)

export const dynamic = 'force-dynamic'
export const runtime = 'nodejs'

const REVIEW_BIN = path.resolve(process.cwd(), '..', '..', 'bin', 'jarvis-evolution-review')

export async function POST(): Promise<Response> {
  try {
    const { stdout } = await execFileP(REVIEW_BIN, ['--all'], {
      timeout: 600_000,
      maxBuffer: 16 * 1024 * 1024,
    })
    let summary: unknown = null
    try {
      summary = JSON.parse(stdout.trim())
    } catch {
      /* council prints JSON on success; non-JSON falls through to summary:null */
    }
    return Response.json({ ok: true, summary })
  } catch (e) {
    const err = e as { stderr?: string; stdout?: string; message?: string }
    return Response.json(
      { ok: false, detail: (err.stderr || err.stdout || err.message || 'review-all failed').trim() },
      { status: 502 },
    )
  }
}
