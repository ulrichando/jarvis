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
  // MONOTONIC: never move the cursor backward. GitHub's ?since= filters on
  // updated_at, so an edited OLD comment re-enters the sweep with an old
  // created_at — advancing to it would regress the window and replay history.
  // ISO-8601 UTC strings compare chronologically as plain strings.
  let existing = ''
  try {
    existing = readFileSync(cursorPath(repo, dir), 'utf8').trim()
  } catch {
    /* no cursor yet */
  }
  if (existing && !Number.isNaN(new Date(existing).getTime()) && iso <= existing) return
  writeFileSync(cursorPath(repo, dir), iso)
}

// Handled comment-id store — the REAL no-replay guarantee. ?since= is
// INCLUSIVE (updated_at >= since), so the last handled mention is re-fetched
// every sweep; the id store is what prevents a duplicate acknowledgement.
const HANDLED_IDS_MAX = 500

function handledPath(repo: string, dir: string): string {
  return join(dir, `${repo.replace(/\//g, '__')}.handled`)
}

export function readHandledIds(repo: string, dir: string = GH_AGENT_DIR): Set<number> {
  const ids = new Set<number>()
  try {
    for (const line of readFileSync(handledPath(repo, dir), 'utf8').split('\n')) {
      const t = line.trim()
      if (/^\d+$/.test(t)) ids.add(Number(t))
    }
  } catch {
    /* missing file → empty set */
  }
  return ids
}

export function addHandledIds(repo: string, ids: number[], dir: string = GH_AGENT_DIR): void {
  mkdirSync(dir, { recursive: true })
  const merged = readHandledIds(repo, dir)
  for (const id of ids) {
    merged.delete(id) // re-adding moves an id to the tail (most-recent)
    merged.add(id)
  }
  // Sets preserve insertion order: file order = oldest→newest, keep the tail.
  const bounded = [...merged].slice(-HANDLED_IDS_MAX)
  writeFileSync(handledPath(repo, dir), bounded.join('\n') + (bounded.length > 0 ? '\n' : ''))
}
