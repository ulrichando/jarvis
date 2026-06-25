/**
 * GET /api/evolution/[id]/log — the build log + test output for one proposal.
 *
 * Returns the tail of the auto-mod build transcript (~/.jarvis/auto-mods/<id>.log)
 * so you can see what was built and whether tests ran/passed. Read-only.
 * Same-origin from the logged-in page (proxy.ts gates it).
 */
import { promises as fs } from 'fs'
import os from 'os'
import path from 'path'

export const dynamic = 'force-dynamic'
export const runtime = 'nodejs'

const AUTOMOD_DIR = path.join(os.homedir(), '.jarvis', 'auto-mods')
const ID_RE = /^automod-[A-Za-z0-9._-]+$/
const MAX = 120_000

export async function GET(
  _req: Request,
  ctx: { params: Promise<{ id: string }> },
): Promise<Response> {
  const { id } = await ctx.params
  if (!ID_RE.test(id)) {
    return Response.json({ ok: false, detail: 'invalid id' }, { status: 400 })
  }
  try {
    const file = path.join(AUTOMOD_DIR, `${id}.log`)
    const buf = await fs.readFile(file, 'utf-8')
    const log = buf.length > MAX ? buf.slice(buf.length - MAX) : buf
    return Response.json({ ok: true, log, truncated: buf.length > MAX })
  } catch {
    return Response.json({ ok: true, log: '', detail: 'no build log found for this proposal' })
  }
}
