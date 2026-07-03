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
    finished_at timestamptz
  )`)
  await sql(`create index if not exists gh_app_jobs_status_idx on gh_app_jobs (status, id)`)
}

export async function enqueue(sql: SqlClient, job: NewJob): Promise<number> {
  const r = await sql(
    `insert into gh_app_jobs (installation_id, repo, issue_number, task, is_pr)
     values ($1, $2, $3, $4, $5) returning id`,
    [job.installationId, job.repo, job.issueNumber, job.task, job.isPR],
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
     returning id, installation_id, repo, issue_number, task, is_pr`,
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
  }
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
  countToday: () => Promise<number>
}

/** Bind all queue ops to one sql client — the shape the worker takes as deps. */
export function jobStore(sql: SqlClient): JobStore {
  return {
    enqueue: (job) => enqueue(sql, job),
    claimNext: () => claimNext(sql),
    markDone: (id) => markDone(sql, id),
    markFailed: (id, error) => markFailed(sql, id, error),
    countToday: () => countToday(sql),
  }
}
