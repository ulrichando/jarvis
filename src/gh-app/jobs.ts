// src/gh-app/jobs.ts — Postgres-backed job queue for the GitHub App.
//
// Injected `sql` client (pg-style: (text, params) => { rows }) so tests use a
// recording fake and the server binds it to the real driver. claimNext is the
// only concurrency-sensitive statement: a single UPDATE with
// FOR UPDATE SKIP LOCKED means N worker slots can claim in parallel without
// double-running a job.

export type SqlClient = (text: string, params?: unknown[]) => Promise<{ rows: any[] }>

export type NewJob = {
  installationId: number
  repo: string
  issueNumber: number
  task: string
  isPR: boolean
  /** id of the triggering comment (issue_comment / review comment) — absent
   * for issues.opened triggers. Feedback reacts 👀 on it. */
  commentId?: number
}
export type Job = NewJob & { id: number }

export async function ensureSchema(sql: SqlClient): Promise<void> {
  await sql(`create table if not exists gh_app_jobs (
    id bigserial primary key,
    installation_id bigint not null,
    repo text not null,
    issue_number int not null,
    task text not null,
    is_pr boolean not null default false,
    status text not null default 'queued',
    error text,
    created_at timestamptz not null default now(),
    started_at timestamptz,
    finished_at timestamptz,
    comment_id bigint,
    tracking_comment_id bigint
  )`)
  // Pre-feedback tables predate the two comment columns — migrate in place
  // (idempotent; no-ops once present).
  await sql(`alter table gh_app_jobs add column if not exists comment_id bigint`)
  await sql(`alter table gh_app_jobs add column if not exists tracking_comment_id bigint`)
  await sql(`create index if not exists gh_app_jobs_status_idx on gh_app_jobs (status, id)`)
}

export async function enqueue(sql: SqlClient, job: NewJob): Promise<number> {
  const r = await sql(
    `insert into gh_app_jobs (installation_id, repo, issue_number, task, is_pr, comment_id)
     values ($1, $2, $3, $4, $5, $6) returning id`,
    [job.installationId, job.repo, job.issueNumber, job.task, job.isPR, job.commentId ?? null],
  )
  return Number(r.rows[0]?.id)
}

export async function claimNext(sql: SqlClient): Promise<Job | null> {
  const r = await sql(
    `update gh_app_jobs set status = 'running', started_at = now()
     where id = (
       select id from gh_app_jobs where status = 'queued'
       order by id limit 1 for update skip locked
     )
     returning id, installation_id, repo, issue_number, task, is_pr, comment_id`,
  )
  const row = r.rows[0]
  if (!row) return null
  return {
    id: Number(row.id),
    installationId: Number(row.installation_id),
    repo: String(row.repo),
    issueNumber: Number(row.issue_number),
    task: String(row.task),
    isPR: Boolean(row.is_pr),
    commentId: row.comment_id == null ? undefined : Number(row.comment_id),
  }
}

/** Persist the id of the "working on it" comment so the outcome edit can
 * find it (and operators can trace the thread from the job row). */
export async function setTrackingComment(sql: SqlClient, id: number, trackingCommentId: number): Promise<void> {
  await sql(`update gh_app_jobs set tracking_comment_id = $1 where id = $2`, [trackingCommentId, id])
}

export async function markDone(sql: SqlClient, id: number): Promise<void> {
  await sql(`update gh_app_jobs set status = 'done', finished_at = now() where id = $1`, [id])
}

export async function markFailed(sql: SqlClient, id: number, error: string): Promise<void> {
  // Cap stored error text — worker errors embed subprocess stderr.
  await sql(`update gh_app_jobs set status = 'failed', error = $1, finished_at = now() where id = $2`, [error.slice(0, 2000), id])
}

/** Runs STARTED today (running/done/failed all count — the daily cap bounds
 * spend on attempts, not just successes). Queued jobs don't count. */
export async function countToday(sql: SqlClient): Promise<number> {
  const r = await sql(`select count(*) as n from gh_app_jobs where started_at >= date_trunc('day', now())`)
  return Number(r.rows[0]?.n ?? 0)
}

export type JobStore = {
  enqueue: (job: NewJob) => Promise<number>
  claimNext: () => Promise<Job | null>
  markDone: (id: number) => Promise<void>
  markFailed: (id: number, error: string) => Promise<void>
  setTrackingComment: (id: number, trackingCommentId: number) => Promise<void>
  countToday: () => Promise<number>
}

/** Bind all queue ops to one sql client — the shape the worker takes as deps. */
export function jobStore(sql: SqlClient): JobStore {
  return {
    enqueue: (job) => enqueue(sql, job),
    claimNext: () => claimNext(sql),
    markDone: (id) => markDone(sql, id),
    markFailed: (id, error) => markFailed(sql, id, error),
    setTrackingComment: (id, trackingCommentId) => setTrackingComment(sql, id, trackingCommentId),
    countToday: () => countToday(sql),
  }
}
