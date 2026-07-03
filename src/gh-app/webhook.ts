// src/gh-app/webhook.ts — POST /webhook: verify → parse → gate → enqueue → 202.
//
// Reuses the gh-action engine's parse + author gate (single source of truth
// for what counts as a trigger and who is trusted): the App webhook payload
// is the same GitHub event shape the Action reads, plus `installation.id`.
//
// Response discipline: 401 ONLY for a bad signature; everything else acks 202
// (GitHub marks non-2xx deliveries as failures and retries — an unauthorized
// author is OUR policy decision, not a delivery failure). Gated-out events
// ack without enqueueing.
import { verifySignature } from './sign.js'
import { parseActionEvent, isAuthorized } from '../cli/src/gh-action/event.js'
import type { NewJob } from './jobs.js'

export type WebhookDeps = {
  secret: string
  allowlist: string[]
  trigger?: string
  enqueue: (job: NewJob) => Promise<unknown>
  log?: (m: string) => void
}

export type WebhookResult = { status: number; body: string }

// Comment events fire for created/edited/deleted; without a workflow-level
// `types: [created]` filter (the Action has one, an App does not), an edit or
// delete of an old "@jarvis …" comment would re-trigger the job. Issues fire
// for opened/labeled/…; parseActionEvent already gates those to `opened`.
const COMMENT_EVENTS = new Set(['issue_comment', 'pull_request_review_comment'])

export async function handleWebhook(
  headers: Record<string, string | undefined>,
  rawBody: string | Uint8Array,
  deps: WebhookDeps,
): Promise<WebhookResult> {
  const h = lowerKeys(headers)
  if (!verifySignature(rawBody, h['x-hub-signature-256'], deps.secret)) {
    deps.log?.('webhook: bad signature — rejected')
    return { status: 401, body: 'signature mismatch' }
  }

  let payload: any
  try {
    payload = JSON.parse(typeof rawBody === 'string' ? rawBody : Buffer.from(rawBody).toString('utf8'))
  } catch {
    return { status: 400, body: 'unparseable body' }
  }

  const eventName = h['x-github-event'] ?? ''
  if (COMMENT_EVENTS.has(eventName) && payload?.action !== 'created') {
    return { status: 202, body: 'ignored (comment action != created)' }
  }

  const repo: string = payload?.repository?.full_name ?? ''
  const ev = parseActionEvent({ eventName, repo, trigger: deps.trigger ?? '@jarvis', payload })
  if (!ev) return { status: 202, body: 'no matching @jarvis event' }

  if (!isAuthorized(ev.association, deps.allowlist, ev.author)) {
    deps.log?.(`webhook: @${ev.author} (${ev.association}) not authorized — ignored`)
    return { status: 202, body: 'author not authorized' }
  }

  const installationId = Number(payload?.installation?.id ?? 0)
  if (!Number.isInteger(installationId) || installationId <= 0) {
    // Without an installation id no token can be minted — ack, don't queue.
    deps.log?.('webhook: event without installation.id — ignored')
    return { status: 202, body: 'no installation id' }
  }

  await deps.enqueue({
    installationId,
    repo: ev.repo,
    issueNumber: ev.issueNumber,
    task: ev.task,
    isPR: ev.isPR,
    commentId: ev.commentId, // undefined for issues.opened — nothing to 👀
  })
  deps.log?.(`webhook: enqueued ${ev.repo}#${ev.issueNumber} from @${ev.author}`)
  return { status: 202, body: 'queued' }
}

function lowerKeys(h: Record<string, string | undefined>): Record<string, string | undefined> {
  const out: Record<string, string | undefined> = {}
  for (const [k, v] of Object.entries(h)) out[k.toLowerCase()] = v
  return out
}
