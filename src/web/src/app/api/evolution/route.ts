/**
 * GET /api/evolution — list pending self-evolution proposals for review.
 *
 * Reads the auto-mod artifacts JARVIS writes to ~/.jarvis/auto-mods/*.json and
 * returns the pending ones (the changes JARVIS has proposed to its own code).
 * The /evolution page renders these as review cards; approving one POSTs to
 * /api/evolution/[id]/approve, which runs the host-side deploy + arms the
 * watchdog. Same-origin from the logged-in page (proxy.ts gates it).
 */
import { promises as fs } from 'fs'
import os from 'os'
import path from 'path'

export const dynamic = 'force-dynamic'
export const runtime = 'nodejs'

const AUTOMOD_DIR = path.join(os.homedir(), '.jarvis', 'auto-mods')

function testsOk(tail: string): boolean {
  const low = (tail || '').toLowerCase()
  return !!low && low.includes('passed') && !low.includes('failed') && !low.includes('error')
}

export async function GET(): Promise<Response> {
  let names: string[]
  try {
    names = (await fs.readdir(AUTOMOD_DIR)).filter(
      (f) => f.startsWith('automod-') && f.endsWith('.json'),
    )
  } catch {
    return Response.json({ proposals: [] })
  }

  const proposals = []
  for (const name of names) {
    try {
      const art = JSON.parse(
        await fs.readFile(path.join(AUTOMOD_DIR, name), 'utf-8'),
      )
      if (art.status !== 'pending') continue
      const intent = String(art.intent ?? '').trim()
      proposals.push({
        id: String(art.id ?? name.replace(/\.json$/, '')),
        title: (intent.split('\n')[0] || 'Self-evolution proposal').slice(0, 100),
        intent,
        files: Array.isArray(art.files_changed) ? art.files_changed : [],
        diffSummary: String(art.diff_summary ?? '').trim(),
        testsOk: testsOk(String(art.test_output_tail ?? '')),
        prUrl: typeof art.pr_url === 'string' ? art.pr_url : null,
        createdAt: typeof art.created_at === 'string' ? art.created_at : null,
      })
    } catch {
      /* skip an unreadable / malformed artifact */
    }
  }
  proposals.sort((a, b) => (b.createdAt ?? '').localeCompare(a.createdAt ?? ''))
  return Response.json({ proposals })
}
