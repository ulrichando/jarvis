// src/cli/src/gh-agent/gh.ts
import { execFileNoThrow } from '../utils/execFileNoThrow.js'

export type GhRunner = (args: string[]) => Promise<{ stdout: string; stderr: string; code: number }>

const defaultRunner: GhRunner = async (args) => {
  const r = await execFileNoThrow('gh', args)
  return { stdout: r.stdout, stderr: r.stderr, code: r.code }
}

// Appended to every ack the agent posts, and filtered out on fetch — the pair
// is what prevents the bot from ever treating its own output as a mention.
export const SELF_MARKER = '<!-- jarvis-gh-agent -->'

export type Mention = {
  id: number
  body: string
  author: string
  createdAt: string
  // GitHub's ?since= filters on updated_at (inclusive), so cursor math must
  // use this field, not createdAt.
  updatedAt: string
  issueNumber: number
  url: string
}

export type MentionSweep = {
  mentions: Mention[]
  // Max updated_at across ALL fetched comments (not just trigger matches) —
  // lets the caller advance the since-window past unrelated chatter.
  // null when the sweep fetched no (well-shaped) comments at all.
  maxUpdatedAt: string | null
}

type RawComment = {
  id: number
  body: string
  user?: { login?: string }
  created_at: string
  updated_at: string
  issue_url?: string
  html_url?: string
}

// Shape guard: only rows with the fields the sweep actually relies on pass.
// The GitHub API is well-behaved, but this loop is unattended — one odd row
// must never throw and kill the whole sweep.
function isRawComment(c: unknown): c is RawComment {
  const x = c as Partial<RawComment> | null
  return (
    typeof x === 'object' &&
    x !== null &&
    typeof x.id === 'number' &&
    typeof x.body === 'string' &&
    typeof x.created_at === 'string' &&
    typeof x.updated_at === 'string'
  )
}

function issueNumberFromUrl(issueUrl: string | undefined): number {
  const m = typeof issueUrl === 'string' ? issueUrl.match(/\/issues\/(\d+)(?:$|[/?#])/) : null
  return m ? Number(m[1]) : 0
}

function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

export async function listMentions(
  repo: string,
  trigger: string,
  sinceIso: string,
  run: GhRunner = defaultRunner,
): Promise<MentionSweep | null> {
  const r = await run([
    'api',
    `repos/${repo}/issues/comments?since=${encodeURIComponent(sinceIso)}&per_page=100&sort=created&direction=asc`,
    '--paginate',
    // Without --slurp, --paginate CONCATENATES pages as `[...][...]`, which
    // JSON.parse rejects → any repo busy enough to page silently returns
    // nothing forever. --slurp wraps pages in one array: [[...],[...]].
    '--slurp',
  ])
  if (r.code !== 0) return null // fetch FAILURE — distinct from an empty window
  let parsed: unknown
  try {
    parsed = JSON.parse(r.stdout)
  } catch {
    return null // garbage stdout is a failure too, not "no mentions"
  }
  // One page → [[...]] → flat(1) → [...]. Non-array JSON (error object) → [].
  const flat: unknown[] = Array.isArray(parsed) ? parsed.flat(1) : []
  const comments = flat.filter(isRawComment)
  const maxUpdatedAt = comments.reduce<string | null>(
    (mx, c) => (mx === null || c.updated_at > mx ? c.updated_at : mx),
    null,
  )
  // Word-boundary trigger match: '@jarvis do x' hits; '@jarvisfan99',
  // '@jarvis-bot', 'me@jarvis' don't. '-' counts as a word char because
  // GitHub logins may contain it.
  const triggerRe = new RegExp(`(?<![\\w-])${escapeRegExp(trigger)}(?![\\w-])`)
  const mentions = comments
    .filter(c => !c.body.includes(SELF_MARKER) && triggerRe.test(c.body))
    .map(c => ({
      id: c.id,
      body: c.body,
      author: c.user?.login ?? '',
      createdAt: c.created_at,
      updatedAt: c.updated_at,
      issueNumber: issueNumberFromUrl(c.issue_url),
      url: c.html_url ?? '',
    }))
    .filter(m => m.issueNumber > 0 && m.author !== '')
  return { mentions, maxUpdatedAt }
}

export async function postComment(
  repo: string,
  issueNumber: number,
  body: string,
  run: GhRunner = defaultRunner,
): Promise<boolean> {
  const r = await run([
    'api',
    '-X',
    'POST',
    `repos/${repo}/issues/${issueNumber}/comments`,
    '-f',
    `body=${body}`,
  ])
  return r.code === 0
}
