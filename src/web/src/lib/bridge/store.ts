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
  /** Owner (JARVIS user id) the registering CLI authenticated as, or null. */
  user_id?: string | null
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
  user_id: string | null
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
-- Per-user CLI auth: maps a long-lived JARVIS token (sent by the CLI on
-- register) to a JARVIS user id. The token-generation endpoint writes these
-- (session-authenticated); register resolves token → user to own the env.
CREATE TABLE IF NOT EXISTS session_inbound (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS session_inbound_session ON session_inbound(session_id, seq);
CREATE TABLE IF NOT EXISTS session_internal_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  subagent INTEGER NOT NULL DEFAULT 0,
  payload_json TEXT NOT NULL,
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS session_internal_events_session ON session_internal_events(session_id, id);
CREATE TABLE IF NOT EXISTS bridge_tokens (
  token TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  last_used_at INTEGER
);
CREATE INDEX IF NOT EXISTS bridge_tokens_user ON bridge_tokens(user_id);
`

export function initSchema(db: Database.Database): void {
  db.exec(SCHEMA)
  // Additive migration for DBs created before per-user CCR: add the
  // environments.user_id column if missing (ALTER throws if it already
  // exists — swallow that specific case).
  try {
    db.exec('ALTER TABLE environments ADD COLUMN user_id TEXT')
  } catch {
    /* column already present */
  }
  // Additive migration (2026-06-12): session titles are a real column, not
  // session_events rows — title events rendered as bare "title" lines in the
  // /code session view, which displays unknown event types by name.
  try {
    db.exec('ALTER TABLE sessions ADD COLUMN title TEXT')
  } catch {
    /* column already present */
  }
  // Additive migrations (2026-06-12, CCR v2 worker endpoints): per-session
  // ingress token (bearer for /v1/code/sessions/{id}/worker/*) and the
  // worker epoch (bumped on register; heartbeats/writes 409 on mismatch).
  try {
    db.exec('ALTER TABLE sessions ADD COLUMN session_token TEXT')
  } catch {
    /* column already present */
  }
  try {
    db.exec(
      'ALTER TABLE sessions ADD COLUMN worker_epoch INTEGER NOT NULL DEFAULT 0',
    )
  } catch {
    /* column already present */
  }
  try {
    db.exec('ALTER TABLE sessions ADD COLUMN worker_state_json TEXT')
  } catch {
    /* column already present */
  }
  // Additive migration (2026-06-12, container sessions): which docker
  // container runs this session ({container, repo}), for archive-time reaping
  // and the /code session header.
  try {
    db.exec('ALTER TABLE sessions ADD COLUMN container_json TEXT')
  } catch {
    /* column already present */
  }
  // FK enforcement is the load-bearing reason CASCADE DELETE works on the
  // `work` table when an environment is deleted. Set explicitly here rather
  // than relying on better-sqlite3's bundled SQLite default.
  db.pragma('foreign_keys = ON')
}

/** Get-or-create the caller's long-lived CLI token (one per user). */
export function getOrCreateBridgeToken(store: Store, userId: string): string {
  const existing = store.db
    .prepare('SELECT token FROM bridge_tokens WHERE user_id = ? LIMIT 1')
    .get(userId) as { token: string } | undefined
  if (existing) return existing.token
  const token = `jbr_${randomBytes(24).toString('base64url')}`
  store.db
    .prepare('INSERT INTO bridge_tokens (token, user_id, created_at) VALUES (?, ?, ?)')
    .run(token, userId, Date.now())
  return token
}

/** Resolve a CLI token to its owning user id, or null. Touches last_used_at. */
export function resolveBridgeToken(store: Store, token: string): string | null {
  const row = store.db
    .prepare('SELECT user_id FROM bridge_tokens WHERE token = ?')
    .get(token) as { user_id: string } | undefined
  if (!row) return null
  store.db
    .prepare('UPDATE bridge_tokens SET last_used_at = ? WHERE token = ?')
    .run(Date.now(), token)
  return row.user_id
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
      `INSERT INTO environments (environment_id, environment_secret, machine_name, directory, branch, git_repo_url, max_sessions, worker_type, user_id, created_at, last_seen_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
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
      input.user_id ?? null,
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

/**
 * Reclaim leased work whose lease has been expired for at least `cutoffMs`
 * milliseconds. Default cutoff is 0 (any expired lease).
 *
 * Caller passes a positive cutoff to give the leasing CLI a grace window
 * after the lease nominally expires before the work is reassigned.
 */
export function reclaimExpiredLeases(
  store: Store,
  envId: string,
  cutoffMs: number = 0,
): number {
  const now = Date.now()
  const cutoff = now - cutoffMs
  const result = store.db
    .prepare(
      `UPDATE work SET state = 'pending', leased_at = NULL, lease_expires_at = NULL
       WHERE environment_id = ? AND state = 'leased' AND lease_expires_at < ?`,
    )
    .run(envId, cutoff)
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

/** Permanently remove a session and all its rows (browser "Delete"). */
export function deleteSession(store: Store, sessionId: string): void {
  const tables = [
    'session_events',
    'session_inbound',
    'session_internal_events',
    'sessions',
  ]
  const tx = store.db.transaction((id: string) => {
    for (const t of tables) {
      // Some session_* tables only exist on newer DBs — guard each.
      try {
        store.db.prepare(`DELETE FROM ${t} WHERE session_id = ?`).run(id)
      } catch {
        /* table absent — skip */
      }
    }
  })
  tx(sessionId)
}

// ── Read paths for the /code web UI (CCR parity, 2026-06-11) ───────────────
// The worker-facing endpoints write environments / work / session_events; the
// UI needs to LIST machines, create a session, dispatch a task, and tail a
// session's event stream. These are the read/dispatch helpers for that.

export interface SessionEventRow {
  rowid: number
  event_id: string
  session_id: string
  type: string
  payload_json: string
  created_at: number
}

export interface SessionRow {
  session_id: string
  environment_id: string | null
  archived: number
  created_at: number
  archived_at: number | null
  title: string | null
  session_token: string | null
  worker_epoch: number
  worker_state_json: string | null
  container_json: string | null
}

/** Record the docker container backing a session ({container, repo}). */
export function setSessionContainer(
  store: Store,
  sessionId: string,
  meta: { container: string; repo: string },
): void {
  store.db
    .prepare('UPDATE sessions SET container_json = ? WHERE session_id = ?')
    .run(JSON.stringify(meta), sessionId)
}

/**
 * Registered machines (workers), most-recently-seen first. When `userId` is
 * given, only that user's machines are returned (per-user CCR scoping).
 */
export function listEnvironments(store: Store, userId?: string | null): EnvironmentRow[] {
  if (userId) {
    return store.db
      .prepare('SELECT * FROM environments WHERE user_id = ? ORDER BY last_seen_at DESC')
      .all(userId) as EnvironmentRow[]
  }
  return store.db
    .prepare('SELECT * FROM environments ORDER BY last_seen_at DESC')
    .all() as EnvironmentRow[]
}

/** Single session by id, or null. */
export function findSession(
  store: Store,
  sessionId: string,
): SessionRow | null {
  const row = store.db
    .prepare('SELECT * FROM sessions WHERE session_id = ?')
    .get(sessionId) as SessionRow | undefined
  return row ?? null
}

/** Idempotently register a UI-initiated session against an environment. */
export function getOrCreateSession(
  store: Store,
  sessionId: string,
  environmentId: string,
  title?: string | null,
): void {
  store.db
    .prepare(
      `INSERT OR IGNORE INTO sessions (session_id, environment_id, archived, created_at, title)
       VALUES (?, ?, 0, ?, ?)`,
    )
    .run(sessionId, environmentId, Date.now(), title ?? null)
}

/** Set/replace a session's display title (CLI retitle via PATCH). */
export function setSessionTitle(
  store: Store,
  sessionId: string,
  title: string,
): void {
  store.db
    .prepare('UPDATE sessions SET title = ? WHERE session_id = ?')
    .run(title, sessionId)
}

/** Store the per-session ingress token (bearer for the worker endpoints). */
export function setSessionToken(
  store: Store,
  sessionId: string,
  token: string,
): void {
  store.db
    .prepare('UPDATE sessions SET session_token = ? WHERE session_id = ?')
    .run(token, sessionId)
}

/** True when `token` is the session's ingress token. */
export function validateSessionToken(
  store: Store,
  sessionId: string,
  token: string,
): boolean {
  const row = findSession(store, sessionId)
  return !!row && !!row.session_token && row.session_token === token
}

/** Register-worker semantics: bump and return the session's worker epoch. */
export function bumpWorkerEpoch(store: Store, sessionId: string): number {
  store.db
    .prepare(
      'UPDATE sessions SET worker_epoch = worker_epoch + 1 WHERE session_id = ?',
    )
    .run(sessionId)
  const row = findSession(store, sessionId)
  return row?.worker_epoch ?? 1
}

/** Single work row scoped to an environment, or null. */
export function findWork(
  store: Store,
  envId: string,
  workId: string,
): WorkRow | null {
  const row = store.db
    .prepare('SELECT * FROM work WHERE id = ? AND environment_id = ?')
    .get(workId, envId) as
    | (Omit<WorkRow, 'data'> & { data_json: string })
    | undefined
  if (!row) return null
  return {
    id: row.id,
    environment_id: row.environment_id,
    session_id: row.session_id,
    state: row.state,
    data: JSON.parse(row.data_json) as unknown,
    secret_b64url: row.secret_b64url,
    leased_at: row.leased_at,
    lease_expires_at: row.lease_expires_at,
    created_at: row.created_at,
  }
}

/**
 * True when `token` is the session ingress token of the session a work item
 * targets. The CLI acks/heartbeats work with the secret's
 * session_ingress_token (NOT the environment secret), so those routes accept
 * either credential.
 */
export function validateWorkSessionToken(
  store: Store,
  envId: string,
  workId: string,
  token: string,
): boolean {
  const row = store.db
    .prepare(
      `SELECT s.session_token AS t FROM work w
       JOIN sessions s ON s.session_id = w.session_id
       WHERE w.id = ? AND w.environment_id = ?`,
    )
    .get(workId, envId) as { t: string | null } | undefined
  return !!row?.t && row.t === token
}

/**
 * True when an inbound (client→worker) event with this uuid exists. Used to
 * drop the worker's echo of a web-sent user message (--replay-user-messages)
 * so the transcript doesn't show the prompt twice.
 */
export function hasInboundUuid(
  store: Store,
  sessionId: string,
  uuid: string,
): boolean {
  const row = store.db
    .prepare(
      `SELECT 1 AS hit FROM session_inbound
       WHERE session_id = ? AND json_extract(payload_json, '$.uuid') = ?
       LIMIT 1`,
    )
    .get(sessionId, uuid) as { hit: number } | undefined
  return !!row
}

/**
 * Merge a CCR v2 worker-state PUT into the session's stored state.
 * Top-level keys replace; external_metadata merges per-key (the CLI clears
 * individual keys by sending explicit nulls).
 */
export function mergeWorkerState(
  store: Store,
  sessionId: string,
  update: Record<string, unknown>,
): void {
  const row = findSession(store, sessionId)
  if (!row) return
  let state: Record<string, unknown> = {}
  try {
    state = row.worker_state_json
      ? (JSON.parse(row.worker_state_json) as Record<string, unknown>)
      : {}
  } catch {
    state = {}
  }
  const { external_metadata, worker_epoch: _epoch, ...rest } = update
  Object.assign(state, rest)
  if (external_metadata && typeof external_metadata === 'object') {
    const merged = {
      ...((state.external_metadata as Record<string, unknown>) ?? {}),
      ...(external_metadata as Record<string, unknown>),
    }
    state.external_metadata = merged
  }
  store.db
    .prepare('UPDATE sessions SET worker_state_json = ? WHERE session_id = ?')
    .run(JSON.stringify(state), sessionId)
}

/** Queue an inbound (web → CLI) payload; returns its sequence number. */
export function appendInbound(
  store: Store,
  sessionId: string,
  payload: unknown,
): number {
  const res = store.db
    .prepare(
      'INSERT INTO session_inbound (session_id, payload_json, created_at) VALUES (?, ?, ?)',
    )
    .run(sessionId, JSON.stringify(payload), Date.now())
  return Number(res.lastInsertRowid)
}

/** Inbound payloads with seq > sinceSeq, oldest first. */
export function listInboundSince(
  store: Store,
  sessionId: string,
  sinceSeq: number,
): Array<{ seq: number; payload_json: string }> {
  return store.db
    .prepare(
      'SELECT seq, payload_json FROM session_inbound WHERE session_id = ? AND seq > ? ORDER BY seq ASC',
    )
    .all(sessionId, sinceSeq) as Array<{ seq: number; payload_json: string }>
}

/** Store worker internal events (session-resume state; not shown in the UI). */
export function appendInternalEvents(
  store: Store,
  sessionId: string,
  events: unknown[],
  subagent: boolean,
): void {
  const insert = store.db.prepare(
    'INSERT INTO session_internal_events (session_id, subagent, payload_json, created_at) VALUES (?, ?, ?, ?)',
  )
  const now = Date.now()
  for (const event of events) {
    insert.run(sessionId, subagent ? 1 : 0, JSON.stringify(event), now)
  }
}

/** All stored internal events for resume (oldest first). */
export function listInternalEvents(
  store: Store,
  sessionId: string,
  subagent: boolean,
): unknown[] {
  const rows = store.db
    .prepare(
      'SELECT payload_json FROM session_internal_events WHERE session_id = ? AND subagent = ? ORDER BY id ASC',
    )
    .all(sessionId, subagent ? 1 : 0) as Array<{ payload_json: string }>
  return rows.map((r) => JSON.parse(r.payload_json) as unknown)
}

/**
 * Sessions, newest first (for the /code sidebar / parallel dashboard). When
 * `userId` is given, only sessions whose environment is owned by that user are
 * returned (per-user scoping).
 */
export function listSessions(store: Store, userId?: string | null): SessionRow[] {
  if (userId) {
    return store.db
      .prepare(
        `SELECT s.* FROM sessions s
         JOIN environments e ON e.environment_id = s.environment_id
         WHERE e.user_id = ?
         ORDER BY s.created_at DESC`,
      )
      .all(userId) as SessionRow[]
  }
  return store.db
    .prepare('SELECT * FROM sessions ORDER BY created_at DESC')
    .all() as SessionRow[]
}

/**
 * A session's events after `sinceRowid` (0 = from the start). rowid is the
 * monotonic insert cursor — unique even for events sharing a created_at ms,
 * so the UI can long-poll without dupes or gaps.
 */
export function listSessionEvents(
  store: Store,
  sessionId: string,
  sinceRowid = 0,
): SessionEventRow[] {
  return store.db
    .prepare(
      `SELECT rowid, event_id, session_id, type, payload_json, created_at
       FROM session_events
       WHERE session_id = ? AND rowid > ?
       ORDER BY rowid ASC`,
    )
    .all(sessionId, sinceRowid) as SessionEventRow[]
}
