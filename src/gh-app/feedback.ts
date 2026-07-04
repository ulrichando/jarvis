// src/gh-app/feedback.ts — visible thread feedback: 👀 + tracking comment.
//
// claude-code-action-style loop: when a job is accepted the bot (1) reacts 👀
// on the triggering comment, (2) posts a "working on it" tracking comment,
// and (3) edits that comment into the final outcome (PR link / no-changes /
// error) — the thread shows visible history instead of a silent PR.
//
// Injected `fetch` (mirror of token.ts); the short-lived installation token
// is passed per call. Everything here is STRICTLY best-effort: these
// functions never throw — a feedback hiccup must never fail the job. All
// posted bodies carry SELF_MARKER (the same symbol the webhook's
// parseActionEvent filters on) so the bot never re-triggers on its own posts.
import { SELF_MARKER } from '../cli/src/gh-agent/gh.js'

export type FeedbackDeps = { fetch: typeof fetch }

/** Outcome of a sandbox run, as the worker sees it. The host-side
 * runInSandbox only populates ok/error today; prUrl/noChanges are optional
 * enrichments (the in-container engine knows them) mapped when present.
 * sessionUrl (Phase C) is set on the code-session path — the watchable
 * /code transcript the outcome message links to. */
export type SandboxResult = { ok: boolean; prUrl?: string; noChanges?: boolean; error?: string; sessionUrl?: string; merged?: { number: number; method: string } }

const API = 'https://api.github.com'

function ghHeaders(token: string): Record<string, string> {
  return {
    Authorization: `Bearer ${token}`,
    Accept: 'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28',
    'User-Agent': 'jarvis-gh-app',
    'content-type': 'application/json',
  }
}

export function workingMessage(task: string, sessionUrl?: string): string {
  // Quote every line so multiline tasks stay inside the blockquote; cap the
  // echo so a huge issue body can't blow the 64 KiB comment limit.
  const quoted = task.slice(0, 6000).split('\n').map((l) => `> ${l}`).join('\n')
  // Code-session path only (Phase C): link the live, watchable /code run.
  // Without a url the sandbox shape stays byte-identical.
  const watch = sessionUrl ? `\n\n▶︎ Watch it live: ${sessionUrl}` : ''
  return `🤖 **talos** is on it — working on:\n\n${quoted}${watch}\n\n${SELF_MARKER}`
}

export function prNumberFromUrl(prUrl: string): number | null {
  const m = /\/pull\/(\d+)(?:[/?#]|$)/.exec(prUrl)
  return m ? Number(m[1]) : null
}

export function resultMessage(r: SandboxResult): string {
  // Code-session path only (Phase C): every outcome links the watchable run.
  // Empty when absent → the sandbox shapes stay byte-identical.
  const watch = r.sessionUrl ? ` · [watch the run](${r.sessionUrl})` : ''
  let body: string
  if (r.ok && r.merged) {
    body = `✅ Merged **#${r.merged.number}** (${r.merged.method}).`
  } else if (r.ok && r.prUrl) {
    const n = prNumberFromUrl(r.prUrl)
    body = n
      ? `✅ Done — opened **#${n}** for this. [Review it](${r.prUrl})${watch}.`
      : `✅ Done — [Review it](${r.prUrl})${watch}.`
  } else if (r.ok && r.noChanges) {
    body = `ℹ️ No changes were needed for this${watch}.`
  } else if (r.ok) {
    body = `✅ Done${watch}.`
  } else {
    // Backticks would break the inline-code span; error text is already
    // token-redacted upstream (runInSandbox) and capped for thread sanity.
    const err = (r.error ?? 'unknown error').replace(/`/g, "'").slice(0, 600)
    body = `⚠️ Couldn't complete this — \`${err}\`${watch}.`
  }
  return `${body}\n\n${SELF_MARKER}`
}

export type AckEvent = { issueNumber: number; task: string; commentId?: number; sessionUrl?: string }

/**
 * 👀 the triggering comment (best-effort — a failed reaction is cosmetic),
 * then post the "working on it" tracking comment. Returns the tracking
 * comment's id, or null when the post failed (report() then falls back to a
 * fresh comment so the outcome still lands in the thread).
 */
export async function acknowledge(repo: string, ev: AckEvent, token: string, deps: FeedbackDeps): Promise<number | null> {
  if (ev.commentId) {
    try {
      await deps.fetch(`${API}/repos/${repo}/issues/comments/${ev.commentId}/reactions`, {
        method: 'POST', headers: ghHeaders(token), body: JSON.stringify({ content: 'eyes' }),
      })
    } catch { /* best-effort */ }
  }
  try {
    const res = await deps.fetch(`${API}/repos/${repo}/issues/${ev.issueNumber}/comments`, {
      method: 'POST', headers: ghHeaders(token), body: JSON.stringify({ body: workingMessage(ev.task, ev.sessionUrl) }),
    })
    if (!res.ok) return null
    const raw = (await res.json()) as { id?: unknown }
    return typeof raw.id === 'number' ? raw.id : null
  } catch {
    return null
  }
}

/**
 * Edit the tracking comment into the final outcome. When there is no
 * tracking comment (the working post failed), fall back to POSTing a fresh
 * issue comment — `issueNumber` is only needed for that fallback.
 */
export async function report(
  repo: string,
  trackingCommentId: number | null | undefined,
  result: SandboxResult,
  token: string,
  deps: FeedbackDeps,
  issueNumber?: number,
): Promise<void> {
  const body = JSON.stringify({ body: resultMessage(result) })
  try {
    if (trackingCommentId) {
      await deps.fetch(`${API}/repos/${repo}/issues/comments/${trackingCommentId}`, {
        method: 'PATCH', headers: ghHeaders(token), body,
      })
    } else if (issueNumber) {
      await deps.fetch(`${API}/repos/${repo}/issues/${issueNumber}/comments`, {
        method: 'POST', headers: ghHeaders(token), body,
      })
    }
  } catch { /* best-effort — feedback must never fail the job */ }
}

/** The job-shaped subset feedback needs (structural — Job satisfies it). */
export type FeedbackJob = { repo: string; issueNumber: number; task: string; commentId?: number }

export type WorkerFeedback = {
  /** sessionUrl (Phase C, code-session path) makes the tracking comment link
   * the live run; omitted on the sandbox path → identical message to today. */
  acknowledge: (job: FeedbackJob, token: string, sessionUrl?: string) => Promise<number | null>
  report: (job: FeedbackJob, trackingCommentId: number | null, result: SandboxResult, token: string) => Promise<void>
}

/** Bind the REST calls to the worker-shaped hooks startWorker takes. */
export function workerFeedback(deps: FeedbackDeps): WorkerFeedback {
  return {
    acknowledge: (job, token, sessionUrl) =>
      acknowledge(job.repo, { issueNumber: job.issueNumber, task: job.task, commentId: job.commentId, sessionUrl }, token, deps),
    report: (job, trackingCommentId, result, token) =>
      report(job.repo, trackingCommentId, result, token, deps, job.issueNumber),
  }
}
