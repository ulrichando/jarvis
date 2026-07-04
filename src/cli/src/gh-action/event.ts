// src/cli/src/gh-action/event.ts
import { SELF_MARKER } from '../gh-agent/gh.js'

// Trigger-strip + NUL/control-char strip (keep \n \t). Mirrors gh-agent's
// taskText — that helper lives extracted in gh-agent/task.ts on the P2 branch
// (_presync_backup_20260702) but is still private inside gh-agent/main.ts on
// this branch, so it's defined locally here rather than modifying gh-agent.
// Collapse into `import { taskText } from '../gh-agent/task.js'` once P2 lands.
export function taskText(body: string, trigger: string): string {
  // Case-insensitive locate: GitHub logins are case-insensitive — autocomplete
  // inserts the canonical casing (@Talos-agents) while humans type lowercase.
  const i = body.toLowerCase().indexOf(trigger.toLowerCase())
  const raw = (i === -1 ? body : body.slice(i + trigger.length)).trim()
  return Array.from(raw)
    .filter((ch) => { const c = ch.charCodeAt(0); return c > 0x1f ? c !== 0x7f : c === 0x0a || c === 0x09 })
    .join('')
}

export type ActionEvent = {
  repo: string
  issueNumber: number
  isPR: boolean
  task: string
  author: string
  association: string
  /** id of the triggering comment (issue_comment / pull_request_review_comment);
   * undefined for issues.opened. Lets feedback react 👀 on the exact trigger. */
  commentId?: number
}

export type ActionCtx = { eventName: string; repo: string; trigger: string; payload: any }

// Build the ctx from the runner's environment (GITHUB_* + the event JSON file).
export function actionCtxFromEnv(readFile: (p: string) => string): ActionCtx | null {
  const eventName = process.env.GITHUB_EVENT_NAME ?? ''
  const repo = process.env.GITHUB_REPOSITORY ?? ''
  const path = process.env.GITHUB_EVENT_PATH ?? ''
  const trigger = process.env.JARVIS_GH_TRIGGER ?? '@jarvis'
  if (!eventName || !repo || !path) return null
  let payload: unknown
  try { payload = JSON.parse(readFile(path)) } catch { return null }
  return { eventName, repo, trigger, payload }
}

export function parseActionEvent(ctx: ActionCtx): ActionEvent | null {
  const { eventName, repo, trigger, payload } = ctx
  let body = '', author = '', association = '', issueNumber = 0, isPR = false
  let commentId: number | undefined
  if (eventName === 'issue_comment' || eventName === 'pull_request_review_comment') {
    const c = payload?.comment
    if (!c) return null
    body = c.body ?? ''; author = c.user?.login ?? ''; association = c.author_association ?? ''
    commentId = typeof c.id === 'number' ? c.id : undefined
    if (eventName === 'pull_request_review_comment') { issueNumber = payload?.pull_request?.number ?? 0; isPR = true }
    else { issueNumber = payload?.issue?.number ?? 0; isPR = !!payload?.issue?.pull_request }
  } else if (eventName === 'issues' && payload?.action === 'opened') {
    const i = payload?.issue
    if (!i) return null
    body = i.body ?? ''; author = i.user?.login ?? ''; association = i.author_association ?? ''; issueNumber = i.number ?? 0
  } else {
    return null
  }
  if (body.includes(SELF_MARKER)) return null                        // never react to our own posts
  // 'i': mentions are case-insensitive on GitHub (@talos-agents == @Talos-agents).
  const triggerRe = new RegExp(`(?<![\\w-])${trigger.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}(?![\\w-])`, 'i')
  if (!triggerRe.test(body)) return null
  const task = taskText(body, trigger)
  if (!task || issueNumber <= 0 || !author) return null
  return { repo, issueNumber, isPR, task, author, association, commentId }
}

const TRUSTED_ASSOC = new Set(['OWNER', 'MEMBER', 'COLLABORATOR'])
export function isAuthorized(association: string, allowlist: string[], login = ''): boolean {
  if (!TRUSTED_ASSOC.has(association)) return false
  if (allowlist.length > 0) return allowlist.some(a => a.toLowerCase() === login.toLowerCase())
  return true
}
