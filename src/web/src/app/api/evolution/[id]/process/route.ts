/**
 * POST /api/evolution/[id]/process — build a queued intent into a reviewable diff.
 *
 * Turns one queued self-evolution intent (in ~/.jarvis/auto-mods/queue.jsonl)
 * into a reviewable proposal, on demand. Spawns the host-side
 * `jarvis-evolution-ondemand <id>` DETACHED — it builds the diff in a disposable
 * worktree (origin/master, unaffected by a dirty tree), runs pytest, and
 * finalize writes an automod-*.json with status=pending, which then renders as a
 * review card (the existing /api/evolution/[id]/approve actuator deploys it).
 *
 * This is an explicit, per-click action, so JARVIS_AUTOMOD_SPAWN_LIVE=1 is
 * injected into the child's env ONLY — the global autonomous loop stays in
 * shadow mode. The build only ever produces a reviewable diff; it never deploys.
 *
 * Injection-safe: the id is validated against a strict pattern and passed via
 * spawn args (no shell). Same-origin from the logged-in page (proxy.ts gates it).
 */
import { spawn } from 'child_process'
import path from 'path'

export const dynamic = 'force-dynamic'
export const runtime = 'nodejs'

// The web app runs from src/web → repo root is two levels up.
const ONDEMAND_BIN = path.resolve(process.cwd(), '..', '..', 'bin', 'jarvis-evolution-ondemand')
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
    const child = spawn(ONDEMAND_BIN, [id], {
      detached: true,
      stdio: 'ignore',
      env: { ...process.env, JARVIS_AUTOMOD_SPAWN_LIVE: '1' },
    })
    child.unref()
    return Response.json(
      { ok: true, detail: 'Building — JARVIS is turning this into a reviewable diff.' },
      { status: 202 },
    )
  } catch (e) {
    const err = e as { message?: string }
    return Response.json(
      { ok: false, detail: (err.message || 'failed to start build').trim() },
      { status: 502 },
    )
  }
}
