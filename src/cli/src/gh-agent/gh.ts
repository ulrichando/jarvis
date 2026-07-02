// src/cli/src/gh-agent/gh.ts
import { execFileNoThrow } from '../utils/execFileNoThrow.js'

export type GhRunner = (args: string[]) => Promise<{ stdout: string; stderr: string; code: number }>

const defaultRunner: GhRunner = async (args) => {
  const r = await execFileNoThrow('gh', args)
  return { stdout: r.stdout, stderr: r.stderr, code: r.code }
}

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

type RawComment = {
  id: number
  body: string
  user?: { login?: string }
  created_at: string
  updated_at: string
  issue_url: string
  html_url: string
}

function issueNumberFromUrl(issueUrl: string): number {
  const m = issueUrl.match(/\/issues\/(\d+)(?:$|[/?#])/)
  return m ? Number(m[1]) : 0
}

export async function listMentions(
  repo: string,
  trigger: string,
  sinceIso: string,
  run: GhRunner = defaultRunner,
): Promise<Mention[]> {
  const r = await run([
    'api',
    `repos/${repo}/issues/comments?since=${sinceIso}&per_page=100&sort=created&direction=asc`,
    '--paginate',
  ])
  if (r.code !== 0) return []
  let raw: RawComment[]
  try {
    raw = JSON.parse(r.stdout) as RawComment[]
  } catch {
    return []
  }
  if (!Array.isArray(raw)) return []
  return raw
    .filter(c => typeof c.body === 'string' && c.body.includes(trigger))
    .map(c => ({
      id: c.id,
      body: c.body,
      author: c.user?.login ?? '',
      createdAt: c.created_at,
      updatedAt: c.updated_at,
      issueNumber: issueNumberFromUrl(c.issue_url),
      url: c.html_url,
    }))
    .filter(m => m.issueNumber > 0 && m.author !== '')
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
