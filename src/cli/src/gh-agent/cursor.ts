// src/cli/src/gh-agent/cursor.ts
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { GH_AGENT_DIR } from './config.js'

// owner/name → owner__name.cursor (filesystem-safe, unambiguous: '/' is the
// only reserved char in a GitHub owner/name and becomes '__').
function cursorPath(repo: string, dir: string): string {
  return join(dir, `${repo.replace(/\//g, '__')}.cursor`)
}

export function readCursor(repo: string, dir: string = GH_AGENT_DIR): string {
  try {
    const v = readFileSync(cursorPath(repo, dir), 'utf8').trim()
    if (v && !Number.isNaN(new Date(v).getTime())) return v
  } catch {
    /* fall through to default */
  }
  // First run: look back 1h so we don't replay the entire repo history, but do
  // catch a mention posted moments before the agent first started.
  return new Date(Date.now() - 60 * 60 * 1000).toISOString()
}

export function advanceCursor(repo: string, iso: string, dir: string = GH_AGENT_DIR): void {
  mkdirSync(dir, { recursive: true })
  writeFileSync(cursorPath(repo, dir), iso)
}
