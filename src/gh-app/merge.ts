// src/gh-app/merge.ts — the bot's "merge this PR" capability.
//
// `@talos merge [squash|merge|rebase]` on a PR → merge it via the GitHub
// API with the repo-scoped installation token. Squash is the default. Pure
// parse + an injected-fetch call, so it's unit-testable with zero GitHub.

export type MergeMethod = 'squash' | 'merge' | 'rebase'

/**
 * Parse a merge command out of the @talos task text. Returns the method
 * (squash by default) when the task is a merge directive — i.e. its first word
 * is "merge" — else null. An explicit `squash | merge | rebase` among the
 * following words overrides the default (`merge rebase`, `merge merge`).
 */
export function parseMergeCommand(task: string): { method: MergeMethod } | null {
  const words = task.trim().toLowerCase().split(/\s+/).filter(Boolean)
  if (words[0] !== 'merge') return null
  const rest = words.slice(1)
  const method: MergeMethod = rest.includes('rebase')
    ? 'rebase'
    : rest.includes('merge')
      ? 'merge'
      : 'squash'
  return { method }
}

export type MergeDeps = { fetch: typeof fetch }

const API = 'https://api.github.com'

/**
 * Merge a PR with the installation token. GitHub refuses an unmergeable PR
 * (405 — merge conflict / required checks failing / review required; 409 — the
 * head moved) — its message is surfaced as the error so the bot can report why.
 */
export async function mergePr(
  repo: string,
  prNumber: number,
  method: MergeMethod,
  token: string,
  deps: MergeDeps,
): Promise<{ ok: true } | { ok: false; error: string }> {
  let res: Response
  try {
    res = await deps.fetch(`${API}/repos/${repo}/pulls/${prNumber}/merge`, {
      method: 'PUT',
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
        'User-Agent': 'jarvis-gh-app',
        'content-type': 'application/json',
      },
      body: JSON.stringify({ merge_method: method }),
    })
  } catch (e) {
    return { ok: false, error: `network error: ${e instanceof Error ? e.message : String(e)}` }
  }
  if (res.ok) return { ok: true }
  let detail = ''
  try {
    const j = (await res.json()) as { message?: string }
    detail = j.message ?? ''
  } catch {
    /* non-JSON body */
  }
  return { ok: false, error: detail || `GitHub ${res.status}` }
}
