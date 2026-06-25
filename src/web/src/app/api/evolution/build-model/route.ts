/**
 * POST /api/evolution/build-model — pick the model evolution BUILDS run on.
 *
 * Writes ~/.jarvis/auto-mods/build-model. The build wrapper
 * (bin/jarvis-automod-impl) reads it and exports JARVIS_AUTOMOD_BUILD_MODEL,
 * which start.sh honors ABOVE the global cli-model — so autonomous builds can
 * use a different model than the interactive CLI / voice. Empty string clears
 * the file → builds inherit the global cli-model. Same-origin (proxy.ts gates).
 */
import { promises as fs } from 'fs'
import os from 'os'
import path from 'path'

export const dynamic = 'force-dynamic'
export const runtime = 'nodejs'

const FILE = path.join(os.homedir(), '.jarvis', 'auto-mods', 'build-model')

// STRONG models only — the build agent does real self-modification. Each must
// be a provider-mapped id in src/cli/scripts/start.sh. "" clears → inherit
// the global cli-model.
const ALLOWED = new Set([
  '',
  'deepseek-v4-pro',
  'claude-opus-4-8',
  'kimi-k2.7-code',
])

export async function POST(req: Request): Promise<Response> {
  let model: string | null = null
  try {
    const body = (await req.json()) as { model?: unknown }
    if (typeof body?.model === 'string') model = body.model.trim()
  } catch {
    /* invalid body handled below */
  }
  if (model === null || !ALLOWED.has(model)) {
    return Response.json({ ok: false, detail: 'unknown or missing model' }, { status: 400 })
  }
  try {
    if (model === '') {
      await fs.rm(FILE, { force: true })
    } else {
      await fs.mkdir(path.dirname(FILE), { recursive: true })
      await fs.writeFile(FILE, `${model}\n`, 'utf-8')
    }
    return Response.json({ ok: true, buildModel: model })
  } catch (e) {
    return Response.json({ ok: false, detail: String((e as Error)?.message ?? e) }, { status: 500 })
  }
}
