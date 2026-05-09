import type Database from 'better-sqlite3'
import { randomBytes } from 'node:crypto'

export interface Store {
  db: Database.Database
}

export interface EnvironmentInput {
  machine_name: string
  directory: string
  branch?: string
  git_repo_url?: string
  max_sessions: number
  worker_type: string
  reuse_id?: string
}

export interface EnvironmentRow {
  environment_id: string
  environment_secret: string
  machine_name: string
  directory: string
  branch: string | null
  git_repo_url: string | null
  max_sessions: number
  worker_type: string
  created_at: number
  last_seen_at: number
}

export interface WorkRow {
  id: string
  environment_id: string
  session_id: string
  state: 'pending' | 'leased' | 'done' | 'stopped'
  data: unknown
  secret_b64url: string
  leased_at: number | null
  lease_expires_at: number | null
  created_at: number
}

export interface EnqueueInput {
  session_id: string
  data: unknown
  secret_b64url?: string
}

const SCHEMA = `
CREATE TABLE IF NOT EXISTS environments (
  environment_id TEXT PRIMARY KEY,
  environment_secret TEXT NOT NULL,
  machine_name TEXT NOT NULL,
  directory TEXT NOT NULL,
  branch TEXT,
  git_repo_url TEXT,
  max_sessions INTEGER NOT NULL DEFAULT 4,
  worker_type TEXT NOT NULL DEFAULT 'jarvis',
  created_at INTEGER NOT NULL,
  last_seen_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS work (
  id TEXT PRIMARY KEY,
  environment_id TEXT NOT NULL REFERENCES environments(environment_id) ON DELETE CASCADE,
  session_id TEXT NOT NULL,
  state TEXT NOT NULL,
  data_json TEXT NOT NULL,
  secret_b64url TEXT NOT NULL,
  leased_at INTEGER,
  lease_expires_at INTEGER,
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS work_env_state ON work(environment_id, state);
CREATE TABLE IF NOT EXISTS session_events (
  event_id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  type TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS session_events_session ON session_events(session_id, created_at);
CREATE TABLE IF NOT EXISTS sessions (
  session_id TEXT PRIMARY KEY,
  -- nullable: archiveSession() can create rows for orphan sessions whose
  -- environment was already unregistered. Spec showed NOT NULL; we relax
  -- it so archive remains idempotent regardless of registration order.
  environment_id TEXT,
  archived INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL,
  archived_at INTEGER
);
`

export function initSchema(db: Database.Database): void {
  db.exec(SCHEMA)
  // FK enforcement is the load-bearing reason CASCADE DELETE works on the
  // `work` table when an environment is deleted. Set explicitly here rather
  // than relying on better-sqlite3's bundled SQLite default.
  db.pragma('foreign_keys = ON')
}

function genId(): string {
  return randomBytes(8).toString('hex')
}

function genSecret(): string {
  return randomBytes(32).toString('base64url')
}

export function createEnvironment(
  store: Store,
  input: EnvironmentInput,
): { environment_id: string; environment_secret: string } {
  if (input.reuse_id) {
    const existing = findEnvironment(store, input.reuse_id)
    if (existing) {
      store.db
        .prepare('UPDATE environments SET last_seen_at = ? WHERE environment_id = ?')
        .run(Date.now(), existing.environment_id)
      return {
        environment_id: existing.environment_id,
        environment_secret: existing.environment_secret,
      }
    }
  }
  const id = genId()
  const secret = genSecret()
  const now = Date.now()
  store.db
    .prepare(
      `INSERT INTO environments (environment_id, environment_secret, machine_name, directory, branch, git_repo_url, max_sessions, worker_type, created_at, last_seen_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    )
    .run(
      id,
      secret,
      input.machine_name,
      input.directory,
      input.branch ?? null,
      input.git_repo_url ?? null,
      input.max_sessions,
      input.worker_type,
      now,
      now,
    )
  return { environment_id: id, environment_secret: secret }
}

export function findEnvironment(
  store: Store,
  envId: string,
): EnvironmentRow | null {
  const row = store.db
    .prepare('SELECT * FROM environments WHERE environment_id = ?')
    .get(envId) as EnvironmentRow | undefined
  return row ?? null
}

export function validateEnvSecret(
  store: Store,
  envId: string,
  secret: string,
): boolean {
  const env = findEnvironment(store, envId)
  if (!env) return false
  return env.environment_secret === secret
}

export function deleteEnvironment(store: Store, envId: string): void {
  store.db
    .prepare('DELETE FROM environments WHERE environment_id = ?')
    .run(envId)
}

export function enqueueWork(
  store: Store,
  envId: string,
  input: EnqueueInput,
): WorkRow {
  const id = genId()
  const now = Date.now()
  store.db
    .prepare(
      `INSERT INTO work (id, environment_id, session_id, state, data_json, secret_b64url, created_at)
       VALUES (?, ?, ?, 'pending', ?, ?, ?)`,
    )
    .run(
      id,
      envId,
      input.session_id,
      JSON.stringify(input.data ?? {}),
      input.secret_b64url ?? '',
      now,
    )
  return {
    id,
    environment_id: envId,
    session_id: input.session_id,
    state: 'pending',
    data: input.data,
    secret_b64url: input.secret_b64url ?? '',
    leased_at: null,
    lease_expires_at: null,
    created_at: now,
  }
}

export function leaseNextWork(
  store: Store,
  envId: string,
  leaseTtlMs: number,
): WorkRow | null {
  return store.db.transaction(() => {
    const row = store.db
      .prepare(
        `SELECT * FROM work
         WHERE environment_id = ? AND state = 'pending'
         ORDER BY created_at ASC LIMIT 1`,
      )
      .get(envId) as
      | (Omit<WorkRow, 'data'> & { data_json: string })
      | undefined
    if (!row) return null
    const now = Date.now()
    const expires = now + leaseTtlMs
    store.db
      .prepare(
        `UPDATE work SET state = 'leased', leased_at = ?, lease_expires_at = ? WHERE id = ?`,
      )
      .run(now, expires, row.id)
    return {
      id: row.id,
      environment_id: row.environment_id,
      session_id: row.session_id,
      state: 'leased' as const,
      data: JSON.parse(row.data_json) as unknown,
      secret_b64url: row.secret_b64url,
      leased_at: now,
      lease_expires_at: expires,
      created_at: row.created_at,
    }
  })()
}

export function reclaimExpiredLeases(store: Store, envId: string): number {
  const now = Date.now()
  const result = store.db
    .prepare(
      `UPDATE work SET state = 'pending', leased_at = NULL, lease_expires_at = NULL
       WHERE environment_id = ? AND state = 'leased' AND lease_expires_at < ?`,
    )
    .run(envId, now)
  return result.changes
}

/**
 * Extend the lease on a leased work row, if the row is still in 'leased'
 * state. Returns `{ lease_extended, state, ttl_seconds }`.
 *
 * Note: `last_heartbeat` (a wire-format ISO-8601 string) is NOT returned
 * here — the route handler at /work/[workId]/heartbeat formats and adds
 * it when building the HeartbeatResponse, since timestamp formatting is
 * a wire-layer concern.
 */
export function heartbeatWork(
  store: Store,
  envId: string,
  workId: string,
  leaseTtlMs: number,
): { lease_extended: boolean; state: string; ttl_seconds: number } {
  const row = store.db
    .prepare('SELECT * FROM work WHERE id = ? AND environment_id = ?')
    .get(workId, envId) as { state: string } | undefined
  if (!row || row.state !== 'leased') {
    return {
      lease_extended: false,
      state: row?.state ?? 'unknown',
      ttl_seconds: 0,
    }
  }
  const now = Date.now()
  const expires = now + leaseTtlMs
  store.db
    .prepare('UPDATE work SET lease_expires_at = ? WHERE id = ?')
    .run(expires, workId)
  return {
    lease_extended: true,
    state: 'leased',
    ttl_seconds: Math.floor(leaseTtlMs / 1000),
  }
}

export function stopWork(store: Store, envId: string, workId: string): void {
  store.db
    .prepare(
      `UPDATE work SET state = 'stopped' WHERE id = ? AND environment_id = ?`,
    )
    .run(workId, envId)
}

export function appendSessionEvent(
  store: Store,
  sessionId: string,
  event: { type: string; payload: unknown },
): void {
  store.db
    .prepare(
      `INSERT INTO session_events (event_id, session_id, type, payload_json, created_at)
       VALUES (?, ?, ?, ?, ?)`,
    )
    .run(
      genId(),
      sessionId,
      event.type,
      JSON.stringify(event.payload ?? {}),
      Date.now(),
    )
}

export function archiveSession(
  store: Store,
  sessionId: string,
): 'archived' | 'already' {
  const row = store.db
    .prepare('SELECT archived FROM sessions WHERE session_id = ?')
    .get(sessionId) as { archived: number } | undefined
  if (row && row.archived) return 'already'
  const now = Date.now()
  if (row) {
    store.db
      .prepare(
        'UPDATE sessions SET archived = 1, archived_at = ? WHERE session_id = ?',
      )
      .run(now, sessionId)
  } else {
    store.db
      .prepare(
        `INSERT INTO sessions (session_id, environment_id, archived, created_at, archived_at)
         VALUES (?, NULL, 1, ?, ?)`,
      )
      .run(sessionId, now, now)
  }
  return 'archived'
}
