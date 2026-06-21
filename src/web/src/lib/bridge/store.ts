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
  config_json: string | null
}

export type NetworkLevel = 'full' | 'trusted' | 'custom' | 'none'

export type EnvironmentConfig = {
  /** Extra env vars passed to every container session for this environment. */
  envVars: Record<string, string>
  /** Bash run before the CLI launches (in addition to a repo .jarvis/setup.sh). */
  setupScript: string
  /** Container egress policy (claude.ai/code network access). `full` = today's
   *  --network=host (default, no proxy). Others route egress through an
   *  allowlist proxy. `custom` adds customAllowlist to the trusted defaults. */
  networkLevel: NetworkLevel
  /** Extra allowed domains for `custom`, e.g. ["api.example.com"]. */
  customAllowlist: string[]
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
  // Additive (2026-06-12, env config): per-environment env vars + setup script
  // applied to container sessions (claude.ai/code environment configuration).
  // JSON: { envVars: Record<string,string>, setupScript: string }.
  try {
    db.exec('ALTER TABLE environments ADD COLUMN config_json TEXT')
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
  // Additive migration (2026-06-12): pin sessions to the top of the /code
  // sidebar (claude.ai "Pin"). 0/1.
  try {
    db.exec('ALTER TABLE sessions ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0')
  } catch {
    /* column already present */
  }
  // Additive (2026-06-12): read/unread (sidebar "Mark as read" clears the
  // status dot) + group assignment ("Move to group").
  try {
    db.exec('ALTER TABLE sessions ADD COLUMN read INTEGER NOT NULL DEFAULT 0')
  } catch {
    /* column already present */
  }
  try {
    db.exec('ALTER TABLE sessions ADD COLUMN group_id TEXT')
  } catch {
    /* column already present */
  }
  // Additive (2026-06-12, auto-fix CI): when autofix=1, a background tick asks
  // the session to fix failing CI; autofix_sha records the last commit fixed so
  // it fires at most once per failing commit.
  try {
    db.exec('ALTER TABLE sessions ADD COLUMN autofix INTEGER NOT NULL DEFAULT 0')
  } catch {
    /* column already present */
  }
  try {
    db.exec('ALTER TABLE sessions ADD COLUMN autofix_sha TEXT')
  } catch {
    /* column already present */
  }
  // Additive (2026-06-12, auto-merge): when automerge=1, the background tick
  // merges the session's PR once all checks pass (claude.ai/code Auto-merge).
  try {
    db.exec('ALTER TABLE sessions ADD COLUMN automerge INTEGER NOT NULL DEFAULT 0')
  } catch {
    /* column already present */
  }
  // Additive (2026-06-12, routine runs): the routine that spawned a session, so
  // a routine's past runs can be listed (claude.ai/code routine detail page).
  try {
    db.exec('ALTER TABLE sessions ADD COLUMN routine_id TEXT')
  } catch {
    /* column already present */
  }
  // Additive (2026-06-13, worker resume): the exact CLI worker launch spec
  // (env + command + workdir) captured at launch, so a worker that died (e.g.
  // a web-server restart drops its SSE connection) can be re-exec'd into its
  // still-running container on reopen — without re-running the original task
  // (the CLI persists its own cursor in CLAUDE_CONFIG_DIR).
  try {
    db.exec('ALTER TABLE sessions ADD COLUMN worker_spec_json TEXT')
  } catch {
    /* column already present */
  }
  // Additive (2026-06-13, worker resume): the inbound sequence a resumed worker
  // starts catch-up FROM. A relaunched CLI worker opens a fresh session and
  // would otherwise replay inbound from seq 0 — re-running the original prompt.
  // resumeContainerWorker raises this to the current inbound tip so a resumed
  // worker comes up idle (ready for NEW messages). 0 on first launch → the
  // seeded prompt is delivered normally.
  try {
    db.exec(
      'ALTER TABLE sessions ADD COLUMN inbound_floor_seq INTEGER NOT NULL DEFAULT 0',
    )
  } catch {
    /* column already present */
  }
  db.exec(`CREATE TABLE IF NOT EXISTS session_groups (
    group_id TEXT PRIMARY KEY,
    user_id TEXT,
    name TEXT NOT NULL,
    created_at INTEGER NOT NULL
  );`)
  // Per-message pins (the /code message "Pin" action), server-synced so they
  // survive across devices/browsers (localStorage was per-browser).
  db.exec(`CREATE TABLE IF NOT EXISTS session_message_pins (
    session_id TEXT NOT NULL,
    uuid TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    PRIMARY KEY (session_id, uuid)
  );`)
  // Routines: templated tasks that run on a schedule / API trigger / GitHub
  // event (decisions-pending §16). trigger_json holds the per-type config
  // ({type:'schedule', cron} | {type:'api', token} | {type:'github', …}).
  db.exec(`CREATE TABLE IF NOT EXISTS routines (
    routine_id TEXT PRIMARY KEY,
    user_id TEXT,
    name TEXT NOT NULL,
    instructions TEXT NOT NULL,
    repo TEXT,
    model TEXT,
    permission_mode TEXT,
    trigger_json TEXT NOT NULL,
    paused INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    last_run_at INTEGER,
    next_run_at INTEGER
  );`)
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
  // Identity of a machine = (owner, machine_name, directory). The CLI mints a
  // fresh environment_id on every `/remote-control`, so without this the same
  // machine registered a NEW row each attach — the picker filled up with
  // duplicate "Moon" entries. Reuse by explicit id first, then by identity.
  const existing =
    (input.reuse_id ? findEnvironment(store, input.reuse_id) : null) ??
    findEnvironmentByIdentity(store, input.user_id ?? null, input.machine_name)
  if (existing) {
    const now = Date.now()
    // Refresh the mutable facets that can change between attaches.
    store.db
      .prepare(
        `UPDATE environments
           SET last_seen_at = ?, branch = ?, git_repo_url = ?,
               worker_type = ?, max_sessions = ?
         WHERE environment_id = ?`,
      )
      .run(
        now,
        input.branch ?? existing.branch,
        input.git_repo_url ?? existing.git_repo_url,
        input.worker_type,
        input.max_sessions,
        existing.environment_id,
      )
    return {
      environment_id: existing.environment_id,
      environment_secret: existing.environment_secret,
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

/** Parse an environment's stored config (env vars + setup script). Always
 *  returns a usable object, even for legacy rows with no config_json. */
export function parseEnvironmentConfig(row: EnvironmentRow | null): EnvironmentConfig {
  const empty: EnvironmentConfig = {
    envVars: {},
    setupScript: '',
    networkLevel: 'full',
    customAllowlist: [],
  }
  if (!row?.config_json) return empty
  try {
    const c = JSON.parse(row.config_json) as Partial<EnvironmentConfig>
    const lvl = c.networkLevel
    return {
      envVars:
        c.envVars && typeof c.envVars === 'object'
          ? (c.envVars as Record<string, string>)
          : {},
      setupScript: typeof c.setupScript === 'string' ? c.setupScript : '',
      networkLevel:
        lvl === 'trusted' || lvl === 'custom' || lvl === 'none' ? lvl : 'full',
      customAllowlist: Array.isArray(c.customAllowlist)
        ? c.customAllowlist.filter((d): d is string => typeof d === 'string')
        : [],
    }
  } catch {
    return empty
  }
}

/** Save an environment's env vars + setup script. */
export function setEnvironmentConfig(
  store: Store,
  envId: string,
  config: EnvironmentConfig,
): void {
  store.db
    .prepare('UPDATE environments SET config_json = ? WHERE environment_id = ?')
    .run(JSON.stringify(config), envId)
}

/** Rename an environment (its display name in the /code picker). */
export function renameEnvironment(store: Store, envId: string, name: string): void {
  store.db
    .prepare('UPDATE environments SET machine_name = ? WHERE environment_id = ?')
    .run(name, envId)
}

/** Find a machine's environment by its natural identity (owner + machine +
 * directory), newest first. Used to dedup re-registration. */
export function findEnvironmentByIdentity(
  store: Store,
  userId: string | null,
  machineName: string,
): EnvironmentRow | null {
  // A machine = (owner, machine_name). Directory is a mutable facet, not
  // identity, so the same box attaching from a different folder reuses its
  // row. Scoped to non-container so cloud sandboxes (which all share
  // machine_name='Cloud container') never collapse into each other or the
  // machine — they keep their own per-repo dedup in environments/cloud.
  const row = store.db
    .prepare(
      `SELECT * FROM environments
       WHERE machine_name = ? AND worker_type != 'container'
         AND (user_id IS ? OR user_id = ?)
       ORDER BY last_seen_at DESC LIMIT 1`,
    )
    .get(machineName, userId, userId) as EnvironmentRow | undefined
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

/** A local machine is "online" if its heartbeat landed within this window. */
export const ONLINE_TTL_MS = 2 * 60 * 1000
/** A container sandbox idle past this long (no active session) is reaped. */
export const SANDBOX_TTL_MS = 24 * 60 * 60 * 1000

export function isEnvironmentOnline(
  row: EnvironmentRow,
  now: number = Date.now(),
): boolean {
  return now - row.last_seen_at < ONLINE_TTL_MS
}

/**
 * Delete container (cloud sandbox) rows idle past SANDBOX_TTL_MS with no active
 * session. Best-effort GC run lazily on GET /environments — the container
 * itself was already reaped on archive; this clears the dangling row. Machine
 * rows (non-container) are never deleted here. Returns the count reaped.
 */
export function reapStaleSandboxes(
  store: Store,
  now: number = Date.now(),
): number {
  const cutoff = now - SANDBOX_TTL_MS
  // Only reap per-repo ephemeral sandboxes (git_repo_url set). Repo-less
  // container rows are persistent, configurable "cloud environments" (the picker's
  // Default + named envs) — never GC them.
  const stale = store.db
    .prepare(
      `SELECT environment_id FROM environments
       WHERE worker_type = 'container' AND git_repo_url IS NOT NULL AND last_seen_at < ?`,
    )
    .all(cutoff) as Array<{ environment_id: string }>
  let reaped = 0
  for (const { environment_id } of stale) {
    const active = store.db
      .prepare(
        `SELECT 1 FROM sessions WHERE environment_id = ? AND archived = 0 LIMIT 1`,
      )
      .get(environment_id)
    if (active) continue
    deleteEnvironment(store, environment_id)
    reaped++
  }
  return reaped
}

/** Ensure the user always has a persistent "Default" cloud environment (the
 *  picker's Cloud → Default, like claude.ai/code). Repo-less container row, so
 *  the reaper leaves it alone; the repo is picked per session. Idempotent. */
export function ensureDefaultCloudEnv(store: Store, userId: string): void {
  const has = store.db
    .prepare(
      `SELECT 1 FROM environments
       WHERE worker_type = 'container' AND git_repo_url IS NULL AND user_id = ? LIMIT 1`,
    )
    .get(userId)
  if (has) return
  createEnvironment(store, {
    machine_name: 'Default',
    directory: '/workspace',
    max_sessions: 4,
    worker_type: 'container',
    user_id: userId,
  })
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

/** Reverse archiveSession — clears the archived flag so the session can be
 *  resumed (the /code "Unarchive" action). No-op if it was not archived. */
export function unarchiveSession(store: Store, sessionId: string): void {
  store.db
    .prepare('UPDATE sessions SET archived = 0, archived_at = NULL WHERE session_id = ?')
    .run(sessionId)
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
  pinned: number
  read: number
  group_id: string | null
  autofix: number
  autofix_sha: string | null
  automerge: number
  routine_id: string | null
  worker_spec_json: string | null
  inbound_floor_seq: number
}

/** Persisted container meta — the git proxy scope + cap token live here, so no
 *  schema change is needed (container_json is a JSON blob we own). */
interface ContainerMeta {
  container?: string
  repo?: string
  extraRepos?: string[]
  gitCapToken?: string
}

function parseContainerMeta(session: SessionRow | null): ContainerMeta {
  if (!session?.container_json) return {}
  try {
    return JSON.parse(session.container_json) as ContainerMeta
  } catch {
    return {}
  }
}

/** Record the docker container backing a session, plus its git proxy scope. */
export function setSessionContainer(
  store: Store,
  sessionId: string,
  meta: { container: string; repo: string; extraRepos?: string[]; gitCapToken?: string },
): void {
  store.db
    .prepare('UPDATE sessions SET container_json = ? WHERE session_id = ?')
    .run(JSON.stringify(meta), sessionId)
}

/** Repos a session's git proxy may touch: primary + extras (verbatim casing). */
export function getSessionGitScope(session: SessionRow): string[] {
  const m = parseContainerMeta(session)
  const out: string[] = []
  if (m.repo) out.push(m.repo)
  for (const r of m.extraRepos ?? []) if (r) out.push(r)
  return out
}

/** True when `token` matches the session's stored git capability token. */
export function validateGitCapToken(store: Store, sessionId: string, token: string): boolean {
  const m = parseContainerMeta(findSession(store, sessionId))
  return !!m.gitCapToken && m.gitCapToken === token
}

/** The persisted CLI worker launch spec — enough to re-exec the worker into an
 *  already-running container on resume (see resumeContainerWorker). */
export interface WorkerSpec {
  /** docker -e environment map (token, routing, epoch hint, …). */
  env: Record<string, string>
  /** The `sh -c` command line that runs the CLI child. */
  cmd: string
  /** Working directory inside the container (the primary repo). */
  workdir: string
}

export function setWorkerSpec(
  store: Store,
  sessionId: string,
  spec: WorkerSpec,
): void {
  store.db
    .prepare('UPDATE sessions SET worker_spec_json = ? WHERE session_id = ?')
    .run(JSON.stringify(spec), sessionId)
}

export function getWorkerSpec(store: Store, sessionId: string): WorkerSpec | null {
  const row = store.db
    .prepare('SELECT worker_spec_json FROM sessions WHERE session_id = ?')
    .get(sessionId) as { worker_spec_json: string | null } | undefined
  if (!row?.worker_spec_json) return null
  try {
    return JSON.parse(row.worker_spec_json) as WorkerSpec
  } catch {
    return null
  }
}

/** Unix-ms timestamp of the session's most recent event, or null if none.
 *  Cheap (indexed MAX) — used by resume to skip sessions that are actively
 *  launching or streaming (recent events) vs. genuinely idle/dead. */
export function latestSessionEventAt(
  store: Store,
  sessionId: string,
): number | null {
  const row = store.db
    .prepare(
      'SELECT MAX(created_at) AS ts FROM session_events WHERE session_id = ?',
    )
    .get(sessionId) as { ts: number | null } | undefined
  return row?.ts ?? null
}

/** Highest inbound sequence for a session (0 if none). */
export function latestInboundSeq(store: Store, sessionId: string): number {
  const row = store.db
    .prepare('SELECT MAX(seq) AS s FROM session_inbound WHERE session_id = ?')
    .get(sessionId) as { s: number | null } | undefined
  return row?.s ?? 0
}

/** The inbound seq a resumed worker should start catch-up AFTER: the last
 *  inbound that belonged to a COMPLETED turn (created at/before the latest
 *  `result` event). This skips already-processed prompts (no re-run) while
 *  still delivering inbound the user sent while the worker was down (pending,
 *  after the last result). 0 when no turn ever completed → deliver everything. */
export function resumeFloorSeq(store: Store, sessionId: string): number {
  const row = store.db
    .prepare(
      `SELECT COALESCE(MAX(i.seq), 0) AS s
       FROM session_inbound i
       WHERE i.session_id = ?
         AND i.created_at <= (
           SELECT COALESCE(MAX(e.created_at), 0)
           FROM session_events e
           WHERE e.session_id = ? AND e.type = 'result'
         )`,
    )
    .get(sessionId, sessionId) as { s: number | null } | undefined
  return row?.s ?? 0
}

/** The inbound seq a (re)connecting worker's SSE catch-up starts AFTER. Raised
 *  by resumeContainerWorker to suppress replay of already-processed inbound. */
export function setInboundFloorSeq(
  store: Store,
  sessionId: string,
  seq: number,
): void {
  store.db
    .prepare('UPDATE sessions SET inbound_floor_seq = ? WHERE session_id = ?')
    .run(seq, sessionId)
}

export function getInboundFloorSeq(store: Store, sessionId: string): number {
  const row = store.db
    .prepare('SELECT inbound_floor_seq FROM sessions WHERE session_id = ?')
    .get(sessionId) as { inbound_floor_seq: number } | undefined
  return row?.inbound_floor_seq ?? 0
}

/** Pinned message uuids for a session (server-synced /code message pins). */
export function listPinnedMessageUuids(store: Store, sessionId: string): string[] {
  return (
    store.db
      .prepare('SELECT uuid FROM session_message_pins WHERE session_id = ?')
      .all(sessionId) as { uuid: string }[]
  ).map((r) => r.uuid)
}

/** Pin/unpin a message. */
export function setMessagePin(
  store: Store,
  sessionId: string,
  uuid: string,
  on: boolean,
): void {
  if (on) {
    store.db
      .prepare(
        'INSERT OR IGNORE INTO session_message_pins (session_id, uuid, created_at) VALUES (?, ?, ?)',
      )
      .run(sessionId, uuid, Date.now())
  } else {
    store.db
      .prepare('DELETE FROM session_message_pins WHERE session_id = ? AND uuid = ?')
      .run(sessionId, uuid)
  }
}

/** Toggle auto-fix-CI for a session (the background tick reads this). */
export function setSessionAutofix(store: Store, sessionId: string, on: boolean): void {
  store.db
    .prepare('UPDATE sessions SET autofix = ? WHERE session_id = ?')
    .run(on ? 1 : 0, sessionId)
}

/** Record the last commit SHA auto-fix acted on (fire once per failing commit). */
export function setSessionAutofixSha(store: Store, sessionId: string, sha: string): void {
  store.db
    .prepare('UPDATE sessions SET autofix_sha = ? WHERE session_id = ?')
    .run(sha, sessionId)
}

/** Sessions with auto-fix-CI enabled (background tick scans these). */
export function listAutofixSessions(store: Store): SessionRow[] {
  return store.db
    .prepare('SELECT * FROM sessions WHERE autofix = 1 AND archived = 0')
    .all() as SessionRow[]
}

/** Toggle auto-merge for a session (the background tick reads this). */
export function setSessionAutomerge(store: Store, sessionId: string, on: boolean): void {
  store.db
    .prepare('UPDATE sessions SET automerge = ? WHERE session_id = ?')
    .run(on ? 1 : 0, sessionId)
}

/** Sessions with auto-merge enabled (background tick merges their PR when green). */
export function listAutomergeSessions(store: Store): SessionRow[] {
  return store.db
    .prepare('SELECT * FROM sessions WHERE automerge = 1 AND archived = 0')
    .all() as SessionRow[]
}

/** Sessions whose container has been idle since `before` (epoch ms) — no
 *  session_event newer than that, container still recorded, not archived. Used
 *  by the idle-reclaim tick to reap abandoned containers + free docker. */
export function listIdleContainerSessions(store: Store, before: number): SessionRow[] {
  return store.db
    .prepare(
      `SELECT s.* FROM sessions s
       WHERE s.container_json IS NOT NULL AND s.archived = 0
       AND COALESCE(
         (SELECT MAX(created_at) FROM session_events e WHERE e.session_id = s.session_id),
         s.created_at
       ) < ?`,
    )
    .all(before) as SessionRow[]
}

/** Clear a session's container record (after its container is reaped). */
export function clearSessionContainer(store: Store, sessionId: string): void {
  store.db
    .prepare('UPDATE sessions SET container_json = NULL WHERE session_id = ?')
    .run(sessionId)
}

/** Tag a session with the routine that spawned it (for the routine run list). */
export function setSessionRoutine(store: Store, sessionId: string, routineId: string): void {
  store.db
    .prepare('UPDATE sessions SET routine_id = ? WHERE session_id = ?')
    .run(routineId, sessionId)
}

/** A routine's past runs (its sessions), newest first. */
export function listRoutineRuns(store: Store, routineId: string, limit = 20): SessionRow[] {
  return store.db
    .prepare(
      'SELECT * FROM sessions WHERE routine_id = ? ORDER BY created_at DESC LIMIT ?',
    )
    .all(routineId, limit) as SessionRow[]
}

/** Append a plain user-text turn to a session (shared by the messages route +
 *  the auto-fix tick): an inbound `user` message the child replays, plus a
 *  `user_prompt` event the /code view renders. */
export function appendUserText(store: Store, sessionId: string, text: string): void {
  const uuid = randomBytes(8).toString('hex')
  appendInbound(store, sessionId, {
    type: 'user',
    uuid,
    session_id: sessionId,
    parent_tool_use_id: null,
    message: { role: 'user', content: [{ type: 'text', text }] },
  })
  appendSessionEvent(store, sessionId, {
    type: 'user_prompt',
    payload: { type: 'user_prompt', prompt: text, uuid },
  })
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
  // Pinned first, then newest. Matches the /code sidebar's display order.
  if (userId) {
    return store.db
      .prepare(
        `SELECT s.* FROM sessions s
         JOIN environments e ON e.environment_id = s.environment_id
         WHERE e.user_id = ?
         ORDER BY s.pinned DESC, s.created_at DESC`,
      )
      .all(userId) as SessionRow[]
  }
  return store.db
    .prepare('SELECT * FROM sessions ORDER BY pinned DESC, created_at DESC')
    .all() as SessionRow[]
}

/** Pin/unpin a session (sidebar "Pin"). */
export function setSessionPinned(
  store: Store,
  sessionId: string,
  pinned: boolean,
): void {
  store.db
    .prepare('UPDATE sessions SET pinned = ? WHERE session_id = ?')
    .run(pinned ? 1 : 0, sessionId)
}

/** Mark a session read/unread (sidebar "Mark as read"). */
export function setSessionRead(
  store: Store,
  sessionId: string,
  read: boolean,
): void {
  store.db
    .prepare('UPDATE sessions SET read = ? WHERE session_id = ?')
    .run(read ? 1 : 0, sessionId)
}

export interface SessionGroupRow {
  group_id: string
  user_id: string | null
  name: string
  created_at: number
}

/** Groups owned by a user (or all, anonymous), newest first. */
export function listGroups(
  store: Store,
  userId?: string | null,
): SessionGroupRow[] {
  if (userId) {
    return store.db
      .prepare(
        'SELECT * FROM session_groups WHERE user_id IS ? OR user_id = ? ORDER BY created_at DESC',
      )
      .all(userId, userId) as SessionGroupRow[]
  }
  return store.db
    .prepare('SELECT * FROM session_groups ORDER BY created_at DESC')
    .all() as SessionGroupRow[]
}

/** Create a named group, returning its id. */
export function createGroup(
  store: Store,
  name: string,
  userId: string | null,
): string {
  const id = genId()
  store.db
    .prepare(
      'INSERT INTO session_groups (group_id, user_id, name, created_at) VALUES (?, ?, ?, ?)',
    )
    .run(id, userId, name, Date.now())
  return id
}

/** Assign a session to a group, or clear it (null). */
export function setSessionGroup(
  store: Store,
  sessionId: string,
  groupId: string | null,
): void {
  store.db
    .prepare('UPDATE sessions SET group_id = ? WHERE session_id = ?')
    .run(groupId, sessionId)
}

// ── Routines (§16) ─────────────────────────────────────────────────────────

/** GitHub-trigger filters (claude.ai/code): a routine fires only when the
 *  delivered event payload matches every set field. */
export type GithubFilters = {
  author?: string
  titleContains?: string
  baseBranch?: string
  headBranch?: string
  labels?: string[]
  isDraft?: boolean
  isMerged?: boolean
}

export type RoutineTrigger =
  // `at` (epoch ms) marks a one-time schedule: fire once at/after that instant,
  // then pause. Otherwise `cron` recurs.
  | { type: 'schedule'; cron: string; label?: string; at?: number }
  | { type: 'api'; token: string }
  | { type: 'github'; events: string[]; filters?: GithubFilters }

export interface RoutineRow {
  routine_id: string
  user_id: string | null
  name: string
  instructions: string
  repo: string | null
  model: string | null
  permission_mode: string | null
  trigger_json: string
  paused: number
  created_at: number
  last_run_at: number | null
  next_run_at: number | null
}

export interface RoutineInput {
  name: string
  instructions: string
  repo?: string | null
  model?: string | null
  permission_mode?: string | null
  trigger: RoutineTrigger
  user_id?: string | null
}

export function listRoutines(
  store: Store,
  userId?: string | null,
): RoutineRow[] {
  if (userId) {
    return store.db
      .prepare(
        'SELECT * FROM routines WHERE user_id IS ? OR user_id = ? ORDER BY created_at DESC',
      )
      .all(userId, userId) as RoutineRow[]
  }
  return store.db
    .prepare('SELECT * FROM routines ORDER BY created_at DESC')
    .all() as RoutineRow[]
}

export function findRoutine(store: Store, id: string): RoutineRow | null {
  const row = store.db
    .prepare('SELECT * FROM routines WHERE routine_id = ?')
    .get(id) as RoutineRow | undefined
  return row ?? null
}

export function createRoutine(store: Store, input: RoutineInput): RoutineRow {
  const id = genId()
  store.db
    .prepare(
      `INSERT INTO routines (routine_id, user_id, name, instructions, repo, model, permission_mode, trigger_json, paused, created_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?)`,
    )
    .run(
      id,
      input.user_id ?? null,
      input.name,
      input.instructions,
      input.repo ?? null,
      input.model ?? null,
      input.permission_mode ?? null,
      JSON.stringify(input.trigger),
      Date.now(),
    )
  return findRoutine(store, id)!
}

export function updateRoutine(
  store: Store,
  id: string,
  patch: { paused?: boolean; name?: string; instructions?: string; last_run_at?: number },
): void {
  const sets: string[] = []
  const vals: unknown[] = []
  if (typeof patch.paused === 'boolean') {
    sets.push('paused = ?')
    vals.push(patch.paused ? 1 : 0)
  }
  if (typeof patch.name === 'string') {
    sets.push('name = ?')
    vals.push(patch.name)
  }
  if (typeof patch.instructions === 'string') {
    sets.push('instructions = ?')
    vals.push(patch.instructions)
  }
  if (typeof patch.last_run_at === 'number') {
    sets.push('last_run_at = ?')
    vals.push(patch.last_run_at)
  }
  if (sets.length === 0) return
  vals.push(id)
  store.db
    .prepare(`UPDATE routines SET ${sets.join(', ')} WHERE routine_id = ?`)
    .run(...vals)
}

export function deleteRoutine(store: Store, id: string): void {
  store.db.prepare('DELETE FROM routines WHERE routine_id = ?').run(id)
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
