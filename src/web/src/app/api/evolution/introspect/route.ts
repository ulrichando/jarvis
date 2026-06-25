/**
 * POST /api/evolution/introspect — run a JARVIS self-assessment now.
 *
 * Runs the host-side `jarvis-evolution-introspect`, which gathers evidence
 * (weak fitness axes, recurring corrections, failed builds) and asks the model
 * to name its own flaws + improvements. One LLM call (~10-40s), so this is
 * synchronous with a generous timeout. The result is also persisted to
 * ~/.jarvis/auto-mods/self_assessment.json (surfaced by GET /api/evolution).
 * Same-origin from the logged-in page (proxy.ts gates it).
 */
import { execFile } from 'child_process'
import path from 'path'
import { promisify } from 'util'

const execFileP = promisify(execFile)

export const dynamic = 'force-dynamic'
export const runtime = 'nodejs'

const INTROSPECT_BIN = path.resolve(process.cwd(), '..', '..', 'bin', 'jarvis-evolution-introspect')

export async function POST(): Promise<Response> {
  try {
    const { stdout } = await execFileP(INTROSPECT_BIN, [], {
      timeout: 70_000,
      maxBuffer: 4 * 1024 * 1024,
    })
    let result: unknown = null
    try {
      result = JSON.parse(stdout.trim())
    } catch {
      result = { error: 'could not parse assessment output' }
    }
    const err = (result as { error?: string })?.error
    return Response.json({ ok: !err, result, detail: err ?? '' }, { status: err ? 502 : 200 })
  } catch (e) {
    const err = e as { stderr?: string; stdout?: string; message?: string }
    return Response.json(
      { ok: false, detail: (err.stderr || err.message || 'self-assessment failed').trim() },
      { status: 502 },
    )
  }
}
