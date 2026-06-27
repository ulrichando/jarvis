/**
 * POST /api/evolution/review-all — kick off the 3-lens review council on EVERY
 * pending proposal IN THE BACKGROUND (detached) and return immediately. The
 * council writes ~/.jarvis/auto-mods/.review-all-status.json as it progresses,
 * which GET /api/evolution surfaces — so the page polls and updates each
 * proposal's verdict INCREMENTALLY instead of blocking on one long request
 * (which would time out and look like "nothing happened").
 *
 * Runs host-side `jarvis-evolution-review --all`. ADVISORY ONLY — never deploys.
 * Same-origin, proxy-gated. No args → no injection surface.
 */
import { spawn } from 'child_process'
import path from 'path'

export const dynamic = 'force-dynamic'
export const runtime = 'nodejs'

const REVIEW_BIN = path.resolve(process.cwd(), '..', '..', 'bin', 'jarvis-evolution-review')

export async function POST(): Promise<Response> {
  try {
    const child = spawn(REVIEW_BIN, ['--all'], { detached: true, stdio: 'ignore' })
    child.unref()
    return Response.json({ ok: true, started: true })
  } catch (e) {
    const err = e as { message?: string }
    return Response.json(
      { ok: false, detail: err.message || 'failed to start review-all' },
      { status: 502 },
    )
  }
}
