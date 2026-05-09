# jarvis-web CCR-compat bridge API — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the 9 `/api/bridge/v1/*` Next.js API routes in `src/web/` so the jarvis CLI's existing bridge client can register and exchange work with jarvis-web (loopback-only) instead of Anthropic CCR.

**Architecture:** SQLite-backed (`~/.jarvis/bridge.db` via `better-sqlite3`); stateless route handlers calling a synchronous data layer; in-memory `EventEmitter` for long-poll wakeups; self-issued bearer secrets (no OAuth). Tests use `vitest` (already in deps).

**Tech Stack:** Next.js 15 App Router, TypeScript, `better-sqlite3` ^12.9.0, `vitest` 2.1.0, `nanoid` for IDs, Node `crypto.randomBytes` for secrets, `node:events` `EventEmitter`.

**Spec:** [docs/superpowers/specs/2026-05-09-jarvis-web-ccr-bridge-design.md](../specs/2026-05-09-jarvis-web-ccr-bridge-design.md)

---

## File structure

| Path | Action | Responsibility |
|:--|:--|:--|
| `src/web/src/lib/bridge/store.ts` | Create | SQLite data layer — schema init, CRUD, lease, heartbeat, archive |
| `src/web/src/lib/bridge/auth.ts` | Create | Pure helpers: parse Bearer header; validate against env or session token |
| `src/web/src/lib/bridge/events.ts` | Create | Process-local `EventEmitter` keyed by env_id; subscribe-with-timeout helper |
| `src/web/src/lib/bridge/types.ts` | Create | Re-export the wire types from the CLI side (mirrors `src/cli/src/bridge/types.ts`) |
| `src/web/src/lib/bridge/errors.ts` | Create | Helper that produces the CCR-compat error JSON `{ error: { type, detail } }` |
| `src/web/src/app/api/bridge/v1/environments/bridge/route.ts` | Create | POST register |
| `src/web/src/app/api/bridge/v1/environments/bridge/[envId]/route.ts` | Create | DELETE unregister |
| `src/web/src/app/api/bridge/v1/environments/[envId]/work/poll/route.ts` | Create | GET long-poll |
| `src/web/src/app/api/bridge/v1/environments/[envId]/work/[workId]/ack/route.ts` | Create | POST ack |
| `src/web/src/app/api/bridge/v1/environments/[envId]/work/[workId]/stop/route.ts` | Create | POST stop |
| `src/web/src/app/api/bridge/v1/environments/[envId]/work/[workId]/heartbeat/route.ts` | Create | POST heartbeat |
| `src/web/src/app/api/bridge/v1/environments/[envId]/bridge/reconnect/route.ts` | Create | POST reconnect |
| `src/web/src/app/api/bridge/v1/sessions/[sessionId]/events/route.ts` | Create | POST permission-response events |
| `src/web/src/app/api/bridge/v1/sessions/[sessionId]/archive/route.ts` | Create | POST archive (idempotent 409) |
| `src/web/src/app/api/bridge/v1/admin/enqueue/route.ts` | Create | POST admin enqueue (test seed) |
| `src/web/tests/bridge/store.test.ts` | Create | Unit tests for `store.ts` |
| `src/web/tests/bridge/auth.test.ts` | Create | Unit tests for `auth.ts` |
| `src/web/tests/bridge/events.test.ts` | Create | Unit tests for `events.ts` |
| `src/web/tests/bridge/integration.test.ts` | Create | E2E happy-path test |

---

## Task 1: Wire types + error helper

**Files:**
- Create: `src/web/src/lib/bridge/types.ts`
- Create: `src/web/src/lib/bridge/errors.ts`
- Create: `src/web/tests/bridge/errors.test.ts`

Establishes the shared TS types used everywhere else, and the error envelope shape that the CLI's `extractErrorDetail` / `extractErrorTypeFromData` parses.

- [ ] **Step 1: Write the failing test for `bridgeError`**

Create `src/web/tests/bridge/errors.test.ts`:

```typescript
import { describe, expect, test } from 'vitest'
import { bridgeError } from '@/lib/bridge/errors'

describe('bridgeError', () => {
  test('builds NextResponse-compatible body and status', async () => {
    const res = bridgeError(401, 'unauthorized', 'Bad token')
    expect(res.status).toBe(401)
    const body = await res.json()
    expect(body).toEqual({
      error: { type: 'unauthorized', detail: 'Bad token' },
    })
  })

  test('omits detail when not provided', async () => {
    const res = bridgeError(404, 'not_found')
    expect(res.status).toBe(404)
    const body = await res.json()
    expect(body).toEqual({ error: { type: 'not_found' } })
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd src/web && npx vitest run tests/bridge/errors.test.ts 2>&1 | tail -10
```

Expected: 2 fails (module not found).

- [ ] **Step 3: Create `types.ts`**

Create `src/web/src/lib/bridge/types.ts`:

```typescript
// Wire types mirroring src/cli/src/bridge/types.ts — kept in sync manually.
// Server keeps these minimal; the CLI is authoritative for the full shape.

export interface RegisterRequest {
  machine_name: string
  directory: string
  branch?: string
  git_repo_url?: string
  max_sessions: number
  metadata?: { worker_type?: string }
  environment_id?: string
}

export interface RegisterResponse {
  environment_id: string
  environment_secret: string
}

export interface WorkResponse {
  id: string
  type: 'work'
  environment_id: string
  state: string
  data: unknown
  secret: string
  created_at: string
}

export interface WorkSecret {
  version: number
  session_ingress_token: string
  api_base_url: string
  sources: unknown[]
  auth: Array<{ type: string; token: string }>
  claude_code_args: Record<string, string> | null
  mcp_config: unknown | null
  environment_variables: Record<string, string> | null
  use_code_sessions: boolean
}

export interface HeartbeatResponse {
  lease_extended: boolean
  state: string
  last_heartbeat: string
  ttl_seconds: number
}
```

- [ ] **Step 4: Create `errors.ts`**

Create `src/web/src/lib/bridge/errors.ts`:

```typescript
import { NextResponse } from 'next/server'

/**
 * Build a CCR-compatible error response. Shape matches what the CLI's
 * `extractErrorDetail` / `extractErrorTypeFromData` parsers expect, so
 * the existing client error handling works unchanged.
 */
export function bridgeError(
  status: number,
  type: string,
  detail?: string,
): NextResponse {
  const body: { error: { type: string; detail?: string } } = {
    error: detail ? { type, detail } : { type },
  }
  return NextResponse.json(body, { status })
}
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd src/web && npx vitest run tests/bridge/errors.test.ts 2>&1 | tail -5
```

Expected: `2 passed`.

- [ ] **Step 6: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/web/src/lib/bridge/types.ts \
        src/web/src/lib/bridge/errors.ts \
        src/web/tests/bridge/errors.test.ts
git commit -m "feat(web-bridge): add shared types + error envelope helper"
```

NO Co-Authored-By trailer or Claude attribution.

---

## Task 2: SQLite data layer (`store.ts`)

**Files:**
- Create: `src/web/src/lib/bridge/store.ts`
- Create: `src/web/tests/bridge/store.test.ts`

The store is the source of truth. Schema, CRUD, lease, heartbeat. Synchronous (better-sqlite3 is sync). Each test gets its own in-memory DB.

- [ ] **Step 1: Write the failing tests**

Create `src/web/tests/bridge/store.test.ts`:

```typescript
import { describe, expect, test, beforeEach } from 'vitest'
import Database from 'better-sqlite3'
import {
  initSchema,
  createEnvironment,
  findEnvironment,
  enqueueWork,
  leaseNextWork,
  reclaimExpiredLeases,
  heartbeatWork,
  stopWork,
  appendSessionEvent,
  archiveSession,
  deleteEnvironment,
  validateEnvSecret,
  type Store,
} from '@/lib/bridge/store'

let store: Store

beforeEach(() => {
  const db = new Database(':memory:')
  initSchema(db)
  store = { db }
})

describe('environments', () => {
  test('createEnvironment generates id + secret', () => {
    const env = createEnvironment(store, {
      machine_name: 'kali',
      directory: '/tmp',
      max_sessions: 4,
      worker_type: 'jarvis',
    })
    expect(env.environment_id).toBeTruthy()
    expect(env.environment_secret).toBeTruthy()
    expect(env.environment_secret.length).toBeGreaterThan(20)
  })

  test('createEnvironment with reuse_id reattaches when id exists', () => {
    const env1 = createEnvironment(store, {
      machine_name: 'kali',
      directory: '/tmp',
      max_sessions: 4,
      worker_type: 'jarvis',
    })
    const env2 = createEnvironment(store, {
      machine_name: 'kali',
      directory: '/tmp',
      max_sessions: 4,
      worker_type: 'jarvis',
      reuse_id: env1.environment_id,
    })
    expect(env2.environment_id).toBe(env1.environment_id)
    expect(env2.environment_secret).toBe(env1.environment_secret)
  })

  test('createEnvironment with unknown reuse_id creates fresh', () => {
    const env = createEnvironment(store, {
      machine_name: 'kali',
      directory: '/tmp',
      max_sessions: 4,
      worker_type: 'jarvis',
      reuse_id: 'nonexistent',
    })
    expect(env.environment_id).not.toBe('nonexistent')
  })

  test('validateEnvSecret returns true on match', () => {
    const env = createEnvironment(store, {
      machine_name: 'kali',
      directory: '/tmp',
      max_sessions: 4,
      worker_type: 'jarvis',
    })
    expect(
      validateEnvSecret(store, env.environment_id, env.environment_secret),
    ).toBe(true)
    expect(validateEnvSecret(store, env.environment_id, 'wrong')).toBe(false)
    expect(validateEnvSecret(store, 'nonexistent', 'x')).toBe(false)
  })

  test('deleteEnvironment cascades work', () => {
    const env = createEnvironment(store, {
      machine_name: 'kali',
      directory: '/tmp',
      max_sessions: 4,
      worker_type: 'jarvis',
    })
    enqueueWork(store, env.environment_id, {
      session_id: 'sess1',
      data: { prompt: 'x' },
    })
    deleteEnvironment(store, env.environment_id)
    expect(findEnvironment(store, env.environment_id)).toBeNull()
    const w = leaseNextWork(store, env.environment_id, 60_000)
    expect(w).toBeNull()
  })
})

describe('work queue', () => {
  let envId: string

  beforeEach(() => {
    const env = createEnvironment(store, {
      machine_name: 'kali',
      directory: '/tmp',
      max_sessions: 4,
      worker_type: 'jarvis',
    })
    envId = env.environment_id
  })

  test('leaseNextWork returns null when queue empty', () => {
    expect(leaseNextWork(store, envId, 60_000)).toBeNull()
  })

  test('leaseNextWork returns oldest pending and marks leased', () => {
    enqueueWork(store, envId, { session_id: 'a', data: { n: 1 } })
    enqueueWork(store, envId, { session_id: 'b', data: { n: 2 } })
    const w1 = leaseNextWork(store, envId, 60_000)
    expect(w1).not.toBeNull()
    expect(w1!.state).toBe('leased')
    // Second lease should pick up the second item, not re-lease w1.
    const w2 = leaseNextWork(store, envId, 60_000)
    expect(w2).not.toBeNull()
    expect(w2!.id).not.toBe(w1!.id)
  })

  test('reclaimExpiredLeases marks expired work pending again', () => {
    enqueueWork(store, envId, { session_id: 'a', data: { n: 1 } })
    leaseNextWork(store, envId, -1_000) // immediately expired
    const reclaimed = reclaimExpiredLeases(store, envId)
    expect(reclaimed).toBe(1)
    const w = leaseNextWork(store, envId, 60_000)
    expect(w).not.toBeNull() // available again
  })

  test('heartbeatWork extends lease', () => {
    enqueueWork(store, envId, { session_id: 'a', data: {} })
    const w = leaseNextWork(store, envId, 60_000)!
    const before = w.lease_expires_at
    // Wait so the new expiry is provably later.
    const result = heartbeatWork(store, envId, w.id, 60_000)
    expect(result.lease_extended).toBe(true)
    expect(result.state).toBe('leased')
    const refreshed = leaseNextWork(store, envId, 60_000) // null — already leased
    expect(refreshed).toBeNull()
  })

  test('heartbeatWork rejects stopped work', () => {
    enqueueWork(store, envId, { session_id: 'a', data: {} })
    const w = leaseNextWork(store, envId, 60_000)!
    stopWork(store, envId, w.id)
    const result = heartbeatWork(store, envId, w.id, 60_000)
    expect(result.lease_extended).toBe(false)
  })

  test('stopWork marks work stopped', () => {
    enqueueWork(store, envId, { session_id: 'a', data: {} })
    const w = leaseNextWork(store, envId, 60_000)!
    stopWork(store, envId, w.id)
    // Subsequent heartbeat should fail.
    expect(heartbeatWork(store, envId, w.id, 60_000).lease_extended).toBe(false)
  })
})

describe('sessions', () => {
  test('appendSessionEvent persists event', () => {
    appendSessionEvent(store, 'sess1', {
      type: 'permission_response',
      payload: { granted: true },
    })
    const events = store.db
      .prepare('SELECT * FROM session_events WHERE session_id = ?')
      .all('sess1')
    expect(events).toHaveLength(1)
  })

  test('archiveSession returns "archived" first time, "already" after', () => {
    expect(archiveSession(store, 'sess1')).toBe('archived')
    expect(archiveSession(store, 'sess1')).toBe('already')
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd src/web && npx vitest run tests/bridge/store.test.ts 2>&1 | tail -10
```

Expected: All fail with `Cannot find module '@/lib/bridge/store'`.

- [ ] **Step 3: Implement `store.ts`**

Create `src/web/src/lib/bridge/store.ts`:

```typescript
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
  environment_id TEXT,
  archived INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL,
  archived_at INTEGER
);
PRAGMA foreign_keys = ON;
`

export function initSchema(db: Database.Database): void {
  db.exec(SCHEMA)
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd src/web && npx vitest run tests/bridge/store.test.ts 2>&1 | tail -10
```

Expected: all `store` tests pass (12 in total).

- [ ] **Step 5: Commit**

```bash
git add src/web/src/lib/bridge/store.ts src/web/tests/bridge/store.test.ts
git commit -m "feat(web-bridge): SQLite data layer (store.ts) with 12 unit tests"
```

NO Co-Authored-By trailer or Claude attribution.

---

## Task 3: Auth helper + event bus

**Files:**
- Create: `src/web/src/lib/bridge/auth.ts`
- Create: `src/web/src/lib/bridge/events.ts`
- Create: `src/web/tests/bridge/auth.test.ts`
- Create: `src/web/tests/bridge/events.test.ts`

Two small utilities. `auth.ts` parses `Authorization: Bearer X` and validates. `events.ts` is the in-memory event bus for long-poll wakeups.

- [ ] **Step 1: Write the failing tests**

Create `src/web/tests/bridge/auth.test.ts`:

```typescript
import { describe, expect, test } from 'vitest'
import { extractBearer } from '@/lib/bridge/auth'

describe('extractBearer', () => {
  test('returns the token from a well-formed header', () => {
    expect(extractBearer('Bearer abc123')).toBe('abc123')
  })

  test('case-insensitive scheme', () => {
    expect(extractBearer('bearer xyz')).toBe('xyz')
    expect(extractBearer('BEARER tok')).toBe('tok')
  })

  test('returns null on missing or malformed', () => {
    expect(extractBearer(null)).toBeNull()
    expect(extractBearer('')).toBeNull()
    expect(extractBearer('Basic abc')).toBeNull()
    expect(extractBearer('Bearer')).toBeNull()
    expect(extractBearer('Bearer  ')).toBeNull()
  })
})
```

Create `src/web/tests/bridge/events.test.ts`:

```typescript
import { describe, expect, test } from 'vitest'
import { emitWorkAvailable, waitForWork } from '@/lib/bridge/events'

describe('event bus', () => {
  test('waitForWork resolves true when emitWorkAvailable fires before timeout', async () => {
    const promise = waitForWork('env1', 1000)
    setTimeout(() => emitWorkAvailable('env1'), 50)
    expect(await promise).toBe(true)
  })

  test('waitForWork resolves false on timeout', async () => {
    expect(await waitForWork('envX', 100)).toBe(false)
  })

  test('events are scoped to env_id', async () => {
    const promise = waitForWork('env1', 200)
    emitWorkAvailable('env2') // wrong env, should not wake env1
    expect(await promise).toBe(false)
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd src/web && npx vitest run tests/bridge/auth.test.ts tests/bridge/events.test.ts 2>&1 | tail -10
```

Expected: 6 fails (modules not found).

- [ ] **Step 3: Implement `auth.ts`**

Create `src/web/src/lib/bridge/auth.ts`:

```typescript
/**
 * Parse `Authorization: Bearer <token>`. Returns the token or null if the
 * header is missing, uses a different scheme, or has an empty token.
 */
export function extractBearer(header: string | null): string | null {
  if (!header) return null
  const match = /^bearer\s+(\S+)\s*$/i.exec(header.trim())
  return match ? match[1] : null
}
```

- [ ] **Step 4: Implement `events.ts`**

Create `src/web/src/lib/bridge/events.ts`:

```typescript
import { EventEmitter } from 'node:events'

const bus = new EventEmitter()
bus.setMaxListeners(0) // unbounded — we listen per-poll

function eventName(envId: string): string {
  return `work-available:${envId}`
}

export function emitWorkAvailable(envId: string): void {
  bus.emit(eventName(envId))
}

/**
 * Wait until either work-available is emitted for this env, or the
 * timeout elapses. Returns true on event, false on timeout. Always
 * unsubscribes the listener so we don't leak.
 */
export function waitForWork(envId: string, timeoutMs: number): Promise<boolean> {
  return new Promise(resolve => {
    let done = false
    const cleanup = (val: boolean) => {
      if (done) return
      done = true
      clearTimeout(timer)
      bus.off(eventName(envId), onEvent)
      resolve(val)
    }
    const onEvent = () => cleanup(true)
    bus.once(eventName(envId), onEvent)
    const timer = setTimeout(() => cleanup(false), timeoutMs)
  })
}
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd src/web && npx vitest run tests/bridge/auth.test.ts tests/bridge/events.test.ts 2>&1 | tail -5
```

Expected: `6 passed`.

- [ ] **Step 6: Commit**

```bash
git add src/web/src/lib/bridge/auth.ts \
        src/web/src/lib/bridge/events.ts \
        src/web/tests/bridge/auth.test.ts \
        src/web/tests/bridge/events.test.ts
git commit -m "feat(web-bridge): bearer-token helper + event bus with 6 tests"
```

NO Co-Authored-By trailer or Claude attribution.

---

## Task 4: Singleton DB accessor + production schema init

**Files:**
- Create: `src/web/src/lib/bridge/db.ts`

Production code paths need a single shared `Store` against `~/.jarvis/bridge.db`. Tests use `:memory:` directly via `new Database(':memory:')` (already established in Task 2). This task adds the production accessor only.

- [ ] **Step 1: Create `db.ts`**

Create `src/web/src/lib/bridge/db.ts`:

```typescript
import Database from 'better-sqlite3'
import * as os from 'node:os'
import * as path from 'node:path'
import { mkdirSync } from 'node:fs'
import { initSchema, type Store } from './store'

let cachedStore: Store | null = null

/**
 * Returns the process-wide shared `Store` against `~/.jarvis/bridge.db`.
 * Lazily initializes the file + schema on first call. Tests that want
 * isolation should construct their own `Store` via `new Database(':memory:')`
 * + `initSchema(db)` and not use this accessor.
 */
export function getStore(): Store {
  if (cachedStore) return cachedStore
  const dir = path.join(os.homedir(), '.jarvis')
  mkdirSync(dir, { recursive: true })
  const db = new Database(path.join(dir, 'bridge.db'))
  db.pragma('journal_mode = WAL')
  initSchema(db)
  cachedStore = { db }
  return cachedStore
}

/** For tests only — wipes the cached store so the next getStore() rebuilds. */
export function _resetForTests(): void {
  cachedStore?.db.close()
  cachedStore = null
}
```

- [ ] **Step 2: Smoke-test via vitest's TS compile path**

Run any existing vitest test — if it loads, the new file's TypeScript compiles too:

```bash
cd src/web && npx vitest run tests/bridge/ 2>&1 | tail -5
```

Expected: existing bridge tests still pass; no compile errors mentioning `db.ts`. If a new error appears, fix the file before continuing.

- [ ] **Step 3: Commit**

```bash
git add src/web/src/lib/bridge/db.ts
git commit -m "feat(web-bridge): singleton ~/.jarvis/bridge.db accessor"
```

---

## Task 5: Register + Unregister routes

**Files:**
- Create: `src/web/src/app/api/bridge/v1/environments/bridge/route.ts`
- Create: `src/web/src/app/api/bridge/v1/environments/bridge/[envId]/route.ts`
- Create: `src/web/tests/bridge/integration.test.ts`

The first end-to-end test that calls a real HTTP route. We use Next.js's route handlers via direct `fetch` against an in-process server, but vitest doesn't trivially do that — instead, we test the handlers as functions by importing them and constructing `Request` objects. This pattern matches what other tests in the project use.

- [ ] **Step 1: Write the failing test**

Create `src/web/tests/bridge/integration.test.ts`:

```typescript
import { describe, expect, test, beforeEach } from 'vitest'
import { _resetForTests } from '@/lib/bridge/db'

beforeEach(() => {
  _resetForTests()
})

describe('register + unregister', () => {
  test('POST /api/bridge/v1/environments/bridge returns id+secret', async () => {
    const { POST } = await import(
      '@/app/api/bridge/v1/environments/bridge/route'
    )
    const req = new Request(
      'http://127.0.0.1:3000/api/bridge/v1/environments/bridge',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          machine_name: 'kali',
          directory: '/tmp',
          max_sessions: 4,
          metadata: { worker_type: 'jarvis' },
        }),
      },
    )
    const res = await POST(req)
    expect(res.status).toBe(200)
    const body = (await res.json()) as {
      environment_id: string
      environment_secret: string
    }
    expect(body.environment_id).toBeTruthy()
    expect(body.environment_secret).toBeTruthy()
  })

  test('POST /environments/bridge with reuse_id returns existing', async () => {
    const { POST } = await import(
      '@/app/api/bridge/v1/environments/bridge/route'
    )
    const make = () =>
      new Request(
        'http://127.0.0.1:3000/api/bridge/v1/environments/bridge',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            machine_name: 'kali',
            directory: '/tmp',
            max_sessions: 4,
            metadata: { worker_type: 'jarvis' },
          }),
        },
      )
    const r1 = (await (await POST(make())).json()) as {
      environment_id: string
      environment_secret: string
    }

    const reuseReq = new Request(
      'http://127.0.0.1:3000/api/bridge/v1/environments/bridge',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          machine_name: 'kali',
          directory: '/tmp',
          max_sessions: 4,
          metadata: { worker_type: 'jarvis' },
          environment_id: r1.environment_id,
        }),
      },
    )
    const r2 = (await (await POST(reuseReq)).json()) as {
      environment_id: string
      environment_secret: string
    }
    expect(r2.environment_id).toBe(r1.environment_id)
    expect(r2.environment_secret).toBe(r1.environment_secret)
  })

  test('POST /environments/bridge rejects missing fields', async () => {
    const { POST } = await import(
      '@/app/api/bridge/v1/environments/bridge/route'
    )
    const req = new Request(
      'http://127.0.0.1:3000/api/bridge/v1/environments/bridge',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      },
    )
    const res = await POST(req)
    expect(res.status).toBe(400)
  })

  test('DELETE /environments/bridge/{id} requires bearer', async () => {
    // First register to get an id+secret
    const reg = await import('@/app/api/bridge/v1/environments/bridge/route')
    const r = await reg.POST(
      new Request(
        'http://127.0.0.1:3000/api/bridge/v1/environments/bridge',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            machine_name: 'kali',
            directory: '/tmp',
            max_sessions: 4,
            metadata: { worker_type: 'jarvis' },
          }),
        },
      ),
    )
    const { environment_id, environment_secret } = (await r.json()) as {
      environment_id: string
      environment_secret: string
    }

    const { DELETE } = await import(
      '@/app/api/bridge/v1/environments/bridge/[envId]/route'
    )

    // Without bearer -> 401
    const noAuth = await DELETE(
      new Request(
        `http://127.0.0.1:3000/api/bridge/v1/environments/bridge/${environment_id}`,
        { method: 'DELETE' },
      ),
      { params: Promise.resolve({ envId: environment_id }) },
    )
    expect(noAuth.status).toBe(401)

    // With bearer -> 204
    const ok = await DELETE(
      new Request(
        `http://127.0.0.1:3000/api/bridge/v1/environments/bridge/${environment_id}`,
        {
          method: 'DELETE',
          headers: { Authorization: `Bearer ${environment_secret}` },
        },
      ),
      { params: Promise.resolve({ envId: environment_id }) },
    )
    expect(ok.status).toBe(204)
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd src/web && npx vitest run tests/bridge/integration.test.ts 2>&1 | tail -10
```

Expected: 4 fails (route modules not found).

- [ ] **Step 3: Implement register handler**

Create `src/web/src/app/api/bridge/v1/environments/bridge/route.ts`:

```typescript
import { NextResponse } from 'next/server'
import { getStore } from '@/lib/bridge/db'
import { createEnvironment } from '@/lib/bridge/store'
import { bridgeError } from '@/lib/bridge/errors'

export async function POST(req: Request): Promise<NextResponse> {
  const body = (await req.json().catch(() => null)) as {
    machine_name?: string
    directory?: string
    branch?: string
    git_repo_url?: string
    max_sessions?: number
    metadata?: { worker_type?: string }
    environment_id?: string
  } | null
  if (
    !body ||
    typeof body.machine_name !== 'string' ||
    typeof body.directory !== 'string' ||
    typeof body.max_sessions !== 'number'
  ) {
    return bridgeError(400, 'invalid_request', 'Missing required fields')
  }
  const store = getStore()
  const result = createEnvironment(store, {
    machine_name: body.machine_name,
    directory: body.directory,
    branch: body.branch,
    git_repo_url: body.git_repo_url,
    max_sessions: body.max_sessions,
    worker_type: body.metadata?.worker_type ?? 'jarvis',
    reuse_id: body.environment_id,
  })
  return NextResponse.json(result, { status: 200 })
}
```

- [ ] **Step 4: Implement unregister handler**

Create `src/web/src/app/api/bridge/v1/environments/bridge/[envId]/route.ts`:

```typescript
import { getStore } from '@/lib/bridge/db'
import {
  deleteEnvironment,
  findEnvironment,
  validateEnvSecret,
} from '@/lib/bridge/store'
import { extractBearer } from '@/lib/bridge/auth'
import { bridgeError } from '@/lib/bridge/errors'

export async function DELETE(
  req: Request,
  ctx: { params: Promise<{ envId: string }> },
): Promise<Response> {
  const { envId } = await ctx.params
  const token = extractBearer(req.headers.get('authorization'))
  if (!token) {
    return bridgeError(401, 'unauthorized', 'Missing bearer token')
  }
  const store = getStore()
  if (!findEnvironment(store, envId)) {
    return bridgeError(404, 'not_found', 'Environment not found')
  }
  if (!validateEnvSecret(store, envId, token)) {
    return bridgeError(401, 'unauthorized', 'Invalid environment_secret')
  }
  deleteEnvironment(store, envId)
  return new Response(null, { status: 204 })
}
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd src/web && npx vitest run tests/bridge/integration.test.ts 2>&1 | tail -10
```

Expected: `4 passed`.

- [ ] **Step 6: Commit**

```bash
git add src/web/src/app/api/bridge/v1/environments/bridge/route.ts \
        src/web/src/app/api/bridge/v1/environments/bridge/\[envId\]/route.ts \
        src/web/tests/bridge/integration.test.ts
git commit -m "feat(web-bridge): POST /environments/bridge + DELETE /{id} with 4 tests"
```

NO Co-Authored-By trailer or Claude attribution.

---

## Task 6: Long-poll route

**Files:**
- Create: `src/web/src/app/api/bridge/v1/environments/[envId]/work/poll/route.ts`
- Modify: `src/web/tests/bridge/integration.test.ts` (append)

GET handler that uses `waitForWork` from the event bus to long-poll up to 25 s. With `reclaim_older_than_ms` query param, reclaims expired leases first.

- [ ] **Step 1: Append failing tests**

Append to `src/web/tests/bridge/integration.test.ts`:

```typescript
import { enqueueWork } from '@/lib/bridge/store'
import { getStore } from '@/lib/bridge/db'
import { emitWorkAvailable } from '@/lib/bridge/events'

async function registerEnv(): Promise<{ environment_id: string; environment_secret: string }> {
  const { POST } = await import('@/app/api/bridge/v1/environments/bridge/route')
  const r = await POST(
    new Request('http://127.0.0.1:3000/api/bridge/v1/environments/bridge', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        machine_name: 'kali',
        directory: '/tmp',
        max_sessions: 4,
        metadata: { worker_type: 'jarvis' },
      }),
    }),
  )
  return r.json() as Promise<{ environment_id: string; environment_secret: string }>
}

describe('poll', () => {
  test('returns null body when no work available within timeout', async () => {
    const { environment_id, environment_secret } = await registerEnv()
    const { GET } = await import(
      '@/app/api/bridge/v1/environments/[envId]/work/poll/route'
    )
    // Use a tiny custom timeout so this test runs fast.
    process.env.BRIDGE_POLL_TIMEOUT_MS = '100'
    const res = await GET(
      new Request(
        `http://127.0.0.1:3000/api/bridge/v1/environments/${environment_id}/work/poll`,
        { headers: { Authorization: `Bearer ${environment_secret}` } },
      ),
      { params: Promise.resolve({ envId: environment_id }) },
    )
    expect(res.status).toBe(200)
    const body = await res.json()
    expect(body).toBeNull()
    delete process.env.BRIDGE_POLL_TIMEOUT_MS
  })

  test('returns leased work envelope when present', async () => {
    const { environment_id, environment_secret } = await registerEnv()
    enqueueWork(getStore(), environment_id, {
      session_id: 'sess1',
      data: { prompt: 'hello' },
    })
    const { GET } = await import(
      '@/app/api/bridge/v1/environments/[envId]/work/poll/route'
    )
    const res = await GET(
      new Request(
        `http://127.0.0.1:3000/api/bridge/v1/environments/${environment_id}/work/poll`,
        { headers: { Authorization: `Bearer ${environment_secret}` } },
      ),
      { params: Promise.resolve({ envId: environment_id }) },
    )
    expect(res.status).toBe(200)
    const body = (await res.json()) as { id: string; state: string; data: { prompt: string } }
    expect(body.state).toBe('leased')
    expect(body.data.prompt).toBe('hello')
  })

  test('long-poll wakes up on emitWorkAvailable', async () => {
    const { environment_id, environment_secret } = await registerEnv()
    process.env.BRIDGE_POLL_TIMEOUT_MS = '5000'
    const { GET } = await import(
      '@/app/api/bridge/v1/environments/[envId]/work/poll/route'
    )
    setTimeout(() => {
      enqueueWork(getStore(), environment_id, {
        session_id: 's',
        data: { x: 1 },
      })
      emitWorkAvailable(environment_id)
    }, 50)
    const t0 = Date.now()
    const res = await GET(
      new Request(
        `http://127.0.0.1:3000/api/bridge/v1/environments/${environment_id}/work/poll`,
        { headers: { Authorization: `Bearer ${environment_secret}` } },
      ),
      { params: Promise.resolve({ envId: environment_id }) },
    )
    const elapsed = Date.now() - t0
    expect(elapsed).toBeLessThan(2000)
    const body = await res.json()
    expect(body).not.toBeNull()
    delete process.env.BRIDGE_POLL_TIMEOUT_MS
  })

  test('poll without bearer returns 401', async () => {
    const { environment_id } = await registerEnv()
    const { GET } = await import(
      '@/app/api/bridge/v1/environments/[envId]/work/poll/route'
    )
    const res = await GET(
      new Request(
        `http://127.0.0.1:3000/api/bridge/v1/environments/${environment_id}/work/poll`,
      ),
      { params: Promise.resolve({ envId: environment_id }) },
    )
    expect(res.status).toBe(401)
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd src/web && npx vitest run tests/bridge/integration.test.ts 2>&1 | tail -10
```

Expected: 4 new fails (poll route module missing).

- [ ] **Step 3: Implement poll route**

Create `src/web/src/app/api/bridge/v1/environments/[envId]/work/poll/route.ts`:

```typescript
import { NextResponse } from 'next/server'
import { getStore } from '@/lib/bridge/db'
import {
  leaseNextWork,
  reclaimExpiredLeases,
  validateEnvSecret,
} from '@/lib/bridge/store'
import { extractBearer } from '@/lib/bridge/auth'
import { waitForWork } from '@/lib/bridge/events'
import { bridgeError } from '@/lib/bridge/errors'

const LEASE_TTL_MS = 60_000
const DEFAULT_POLL_TIMEOUT_MS = 25_000

function pollTimeoutMs(): number {
  const env = process.env.BRIDGE_POLL_TIMEOUT_MS
  if (!env) return DEFAULT_POLL_TIMEOUT_MS
  const n = parseInt(env, 10)
  return Number.isFinite(n) && n > 0 ? n : DEFAULT_POLL_TIMEOUT_MS
}

export async function GET(
  req: Request,
  ctx: { params: Promise<{ envId: string }> },
): Promise<NextResponse> {
  const { envId } = await ctx.params
  const token = extractBearer(req.headers.get('authorization'))
  if (!token) return bridgeError(401, 'unauthorized', 'Missing bearer token')
  const store = getStore()
  if (!validateEnvSecret(store, envId, token)) {
    return bridgeError(401, 'unauthorized', 'Invalid environment_secret')
  }

  const url = new URL(req.url)
  const reclaimRaw = url.searchParams.get('reclaim_older_than_ms')
  if (reclaimRaw) {
    reclaimExpiredLeases(store, envId)
  }

  const tryLease = (): NextResponse | null => {
    const work = leaseNextWork(store, envId, LEASE_TTL_MS)
    if (!work) return null
    return NextResponse.json(
      {
        id: work.id,
        type: 'work',
        environment_id: work.environment_id,
        state: work.state,
        data: work.data,
        secret: work.secret_b64url,
        created_at: new Date(work.created_at).toISOString(),
      },
      { status: 200 },
    )
  }

  const immediate = tryLease()
  if (immediate) return immediate

  const woke = await waitForWork(envId, pollTimeoutMs())
  if (woke) {
    const after = tryLease()
    if (after) return after
  }
  return NextResponse.json(null, { status: 200 })
}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd src/web && npx vitest run tests/bridge/integration.test.ts 2>&1 | tail -10
```

Expected: all 8 integration tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/web/src/app/api/bridge/v1/environments/\[envId\]/work/poll/route.ts \
        src/web/tests/bridge/integration.test.ts
git commit -m "feat(web-bridge): GET /work/poll with long-poll + lease + reclaim"
```

---

## Task 7: Ack / Stop / Heartbeat

**Files:**
- Create: `src/web/src/app/api/bridge/v1/environments/[envId]/work/[workId]/ack/route.ts`
- Create: `src/web/src/app/api/bridge/v1/environments/[envId]/work/[workId]/stop/route.ts`
- Create: `src/web/src/app/api/bridge/v1/environments/[envId]/work/[workId]/heartbeat/route.ts`
- Modify: `src/web/tests/bridge/integration.test.ts` (append)

Three small endpoints with the same auth pattern.

- [ ] **Step 1: Append tests**

Append to `src/web/tests/bridge/integration.test.ts`:

```typescript
async function registerAndLeaseWork(): Promise<{
  envId: string
  envSecret: string
  workId: string
}> {
  const reg = await registerEnv()
  enqueueWork(getStore(), reg.environment_id, {
    session_id: 'sess1',
    data: { p: 'x' },
  })
  const { GET } = await import(
    '@/app/api/bridge/v1/environments/[envId]/work/poll/route'
  )
  const r = await GET(
    new Request(
      `http://127.0.0.1:3000/api/bridge/v1/environments/${reg.environment_id}/work/poll`,
      { headers: { Authorization: `Bearer ${reg.environment_secret}` } },
    ),
    { params: Promise.resolve({ envId: reg.environment_id }) },
  )
  const w = (await r.json()) as { id: string }
  return {
    envId: reg.environment_id,
    envSecret: reg.environment_secret,
    workId: w.id,
  }
}

describe('ack/stop/heartbeat', () => {
  test('ack returns 204', async () => {
    const { envId, envSecret, workId } = await registerAndLeaseWork()
    const { POST } = await import(
      '@/app/api/bridge/v1/environments/[envId]/work/[workId]/ack/route'
    )
    const res = await POST(
      new Request(`http://x/`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${envSecret}` },
      }),
      { params: Promise.resolve({ envId, workId }) },
    )
    expect(res.status).toBe(204)
  })

  test('stop returns 204 and marks work stopped', async () => {
    const { envId, envSecret, workId } = await registerAndLeaseWork()
    const { POST } = await import(
      '@/app/api/bridge/v1/environments/[envId]/work/[workId]/stop/route'
    )
    const res = await POST(
      new Request(`http://x/`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${envSecret}`,
        },
        body: JSON.stringify({ force: true }),
      }),
      { params: Promise.resolve({ envId, workId }) },
    )
    expect(res.status).toBe(204)
  })

  test('heartbeat extends lease', async () => {
    const { envId, envSecret, workId } = await registerAndLeaseWork()
    const { POST } = await import(
      '@/app/api/bridge/v1/environments/[envId]/work/[workId]/heartbeat/route'
    )
    const res = await POST(
      new Request(`http://x/`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${envSecret}`,
        },
        body: JSON.stringify({}),
      }),
      { params: Promise.resolve({ envId, workId }) },
    )
    expect(res.status).toBe(200)
    const body = (await res.json()) as { lease_extended: boolean }
    expect(body.lease_extended).toBe(true)
  })

  test('heartbeat after stop returns lease_extended:false', async () => {
    const { envId, envSecret, workId } = await registerAndLeaseWork()
    const stopMod = await import(
      '@/app/api/bridge/v1/environments/[envId]/work/[workId]/stop/route'
    )
    await stopMod.POST(
      new Request(`http://x/`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${envSecret}`,
        },
        body: JSON.stringify({ force: false }),
      }),
      { params: Promise.resolve({ envId, workId }) },
    )
    const hb = await import(
      '@/app/api/bridge/v1/environments/[envId]/work/[workId]/heartbeat/route'
    )
    const res = await hb.POST(
      new Request(`http://x/`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${envSecret}`,
        },
        body: JSON.stringify({}),
      }),
      { params: Promise.resolve({ envId, workId }) },
    )
    const body = (await res.json()) as { lease_extended: boolean }
    expect(body.lease_extended).toBe(false)
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

Expected: 4 new fails (modules missing).

- [ ] **Step 3: Implement ack route**

Create `src/web/src/app/api/bridge/v1/environments/[envId]/work/[workId]/ack/route.ts`:

```typescript
import { extractBearer } from '@/lib/bridge/auth'
import { getStore } from '@/lib/bridge/db'
import { validateEnvSecret } from '@/lib/bridge/store'
import { bridgeError } from '@/lib/bridge/errors'

export async function POST(
  req: Request,
  ctx: { params: Promise<{ envId: string; workId: string }> },
): Promise<Response> {
  const { envId } = await ctx.params
  const token = extractBearer(req.headers.get('authorization'))
  if (!token) return bridgeError(401, 'unauthorized', 'Missing bearer')
  if (!validateEnvSecret(getStore(), envId, token)) {
    return bridgeError(401, 'unauthorized', 'Invalid environment_secret')
  }
  // Ack is a no-op on the server side beyond auth — the lease was already
  // taken by /work/poll. Just return 204 to confirm the CLI is alive.
  return new Response(null, { status: 204 })
}
```

- [ ] **Step 4: Implement stop route**

Create `src/web/src/app/api/bridge/v1/environments/[envId]/work/[workId]/stop/route.ts`:

```typescript
import { extractBearer } from '@/lib/bridge/auth'
import { getStore } from '@/lib/bridge/db'
import { stopWork, validateEnvSecret } from '@/lib/bridge/store'
import { bridgeError } from '@/lib/bridge/errors'

export async function POST(
  req: Request,
  ctx: { params: Promise<{ envId: string; workId: string }> },
): Promise<Response> {
  const { envId, workId } = await ctx.params
  const token = extractBearer(req.headers.get('authorization'))
  if (!token) return bridgeError(401, 'unauthorized', 'Missing bearer')
  if (!validateEnvSecret(getStore(), envId, token)) {
    return bridgeError(401, 'unauthorized', 'Invalid environment_secret')
  }
  stopWork(getStore(), envId, workId)
  return new Response(null, { status: 204 })
}
```

- [ ] **Step 5: Implement heartbeat route**

Create `src/web/src/app/api/bridge/v1/environments/[envId]/work/[workId]/heartbeat/route.ts`:

```typescript
import { NextResponse } from 'next/server'
import { extractBearer } from '@/lib/bridge/auth'
import { getStore } from '@/lib/bridge/db'
import { heartbeatWork, validateEnvSecret } from '@/lib/bridge/store'
import { bridgeError } from '@/lib/bridge/errors'

const LEASE_TTL_MS = 60_000

export async function POST(
  req: Request,
  ctx: { params: Promise<{ envId: string; workId: string }> },
): Promise<Response> {
  const { envId, workId } = await ctx.params
  const token = extractBearer(req.headers.get('authorization'))
  if (!token) return bridgeError(401, 'unauthorized', 'Missing bearer')
  if (!validateEnvSecret(getStore(), envId, token)) {
    return bridgeError(401, 'unauthorized', 'Invalid environment_secret')
  }
  const result = heartbeatWork(getStore(), envId, workId, LEASE_TTL_MS)
  return NextResponse.json(
    {
      lease_extended: result.lease_extended,
      state: result.state,
      last_heartbeat: new Date().toISOString(),
      ttl_seconds: result.ttl_seconds,
    },
    { status: 200 },
  )
}
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
cd src/web && npx vitest run tests/bridge/integration.test.ts 2>&1 | tail -10
```

Expected: 12 integration tests pass total.

- [ ] **Step 7: Commit**

```bash
git add src/web/src/app/api/bridge/v1/environments/\[envId\]/work/\[workId\]/ \
        src/web/tests/bridge/integration.test.ts
git commit -m "feat(web-bridge): ack/stop/heartbeat routes with 4 integration tests"
```

---

## Task 8: Reconnect + Session events + Archive routes

**Files:**
- Create: `src/web/src/app/api/bridge/v1/environments/[envId]/bridge/reconnect/route.ts`
- Create: `src/web/src/app/api/bridge/v1/sessions/[sessionId]/events/route.ts`
- Create: `src/web/src/app/api/bridge/v1/sessions/[sessionId]/archive/route.ts`
- Modify: `src/web/tests/bridge/integration.test.ts` (append)

- [ ] **Step 1: Append tests**

Append:

```typescript
describe('reconnect + events + archive', () => {
  test('reconnect 204 with valid env bearer', async () => {
    const { environment_id, environment_secret } = await registerEnv()
    const { POST } = await import(
      '@/app/api/bridge/v1/environments/[envId]/bridge/reconnect/route'
    )
    const res = await POST(
      new Request(`http://x/`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${environment_secret}`,
        },
        body: JSON.stringify({ session_id: 'sess1' }),
      }),
      { params: Promise.resolve({ envId: environment_id }) },
    )
    expect(res.status).toBe(204)
  })

  test('events route accepts events and returns 204', async () => {
    const { POST } = await import(
      '@/app/api/bridge/v1/sessions/[sessionId]/events/route'
    )
    const res = await POST(
      new Request(`http://x/`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          // Session ingress token check is bypassed in v1 — any non-empty token accepts
          Authorization: 'Bearer any-token',
        },
        body: JSON.stringify({
          events: [{ type: 'permission_response', granted: true }],
        }),
      }),
      { params: Promise.resolve({ sessionId: 'sess1' }) },
    )
    expect(res.status).toBe(204)
  })

  test('archive 204 first time, 409 second time', async () => {
    const { environment_id, environment_secret } = await registerEnv()
    const { POST } = await import(
      '@/app/api/bridge/v1/sessions/[sessionId]/archive/route'
    )
    const make = () =>
      POST(
        new Request(`http://x/`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${environment_secret}`,
          },
          body: '{}',
        }),
        { params: Promise.resolve({ sessionId: 'sessA' }) },
      )
    const r1 = await make()
    expect(r1.status).toBe(204)
    const r2 = await make()
    expect(r2.status).toBe(409)
    // Pin the env id reference
    expect(environment_id).toBeTruthy()
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

Expected: 3 new fails.

- [ ] **Step 3: Implement reconnect route**

Create `src/web/src/app/api/bridge/v1/environments/[envId]/bridge/reconnect/route.ts`:

```typescript
import { extractBearer } from '@/lib/bridge/auth'
import { getStore } from '@/lib/bridge/db'
import { findEnvironment, validateEnvSecret } from '@/lib/bridge/store'
import { bridgeError } from '@/lib/bridge/errors'

export async function POST(
  req: Request,
  ctx: { params: Promise<{ envId: string }> },
): Promise<Response> {
  const { envId } = await ctx.params
  const token = extractBearer(req.headers.get('authorization'))
  if (!token) return bridgeError(401, 'unauthorized', 'Missing bearer')
  const store = getStore()
  if (!findEnvironment(store, envId)) {
    return bridgeError(404, 'not_found', 'Environment not found')
  }
  if (!validateEnvSecret(store, envId, token)) {
    return bridgeError(401, 'unauthorized', 'Invalid environment_secret')
  }
  // Bump last_seen_at
  store.db
    .prepare('UPDATE environments SET last_seen_at = ? WHERE environment_id = ?')
    .run(Date.now(), envId)
  return new Response(null, { status: 204 })
}
```

- [ ] **Step 4: Implement session events route**

Create `src/web/src/app/api/bridge/v1/sessions/[sessionId]/events/route.ts`:

```typescript
import { extractBearer } from '@/lib/bridge/auth'
import { getStore } from '@/lib/bridge/db'
import { appendSessionEvent } from '@/lib/bridge/store'
import { bridgeError } from '@/lib/bridge/errors'

export async function POST(
  req: Request,
  ctx: { params: Promise<{ sessionId: string }> },
): Promise<Response> {
  const { sessionId } = await ctx.params
  const token = extractBearer(req.headers.get('authorization'))
  // v1: accept any non-empty bearer for session events. Sub-project 3 will
  // tighten this to validate against the work row's secret_b64url
  // (session_ingress_token).
  if (!token) return bridgeError(401, 'unauthorized', 'Missing bearer')
  const body = (await req.json().catch(() => null)) as {
    events?: Array<{ type: string; [k: string]: unknown }>
  } | null
  if (!body || !Array.isArray(body.events)) {
    return bridgeError(400, 'invalid_request', 'events array required')
  }
  const store = getStore()
  for (const event of body.events) {
    if (typeof event.type !== 'string') continue
    appendSessionEvent(store, sessionId, {
      type: event.type,
      payload: event,
    })
  }
  return new Response(null, { status: 204 })
}
```

- [ ] **Step 5: Implement archive route**

Create `src/web/src/app/api/bridge/v1/sessions/[sessionId]/archive/route.ts`:

```typescript
import { extractBearer } from '@/lib/bridge/auth'
import { getStore } from '@/lib/bridge/db'
import { archiveSession } from '@/lib/bridge/store'
import { bridgeError } from '@/lib/bridge/errors'

export async function POST(
  req: Request,
  ctx: { params: Promise<{ sessionId: string }> },
): Promise<Response> {
  const { sessionId } = await ctx.params
  const token = extractBearer(req.headers.get('authorization'))
  if (!token) return bridgeError(401, 'unauthorized', 'Missing bearer')
  const result = archiveSession(getStore(), sessionId)
  return new Response(null, { status: result === 'already' ? 409 : 204 })
}
```

- [ ] **Step 6: Run tests to verify they pass**

Expected: 15 integration tests pass total.

- [ ] **Step 7: Commit**

```bash
git add src/web/src/app/api/bridge/v1/environments/\[envId\]/bridge/ \
        src/web/src/app/api/bridge/v1/sessions/ \
        src/web/tests/bridge/integration.test.ts
git commit -m "feat(web-bridge): reconnect + session events + archive routes (3 tests)"
```

---

## Task 9: Admin enqueue route + happy-path E2E

**Files:**
- Create: `src/web/src/app/api/bridge/v1/admin/enqueue/route.ts`
- Modify: `src/web/tests/bridge/integration.test.ts` (append final E2E)

The admin enqueue is a test-seeding helper plus a deliberate hook for sub-project 3 (web UI submitting prompts). Loopback-only, no auth gate (relies on the loopback bind). Then an E2E that exercises the full register→enqueue→poll→ack→heartbeat→events→archive→unregister chain.

- [ ] **Step 1: Append tests**

Append:

```typescript
describe('admin enqueue + full E2E', () => {
  test('admin enqueue returns 200 + work_id', async () => {
    const { environment_id } = await registerEnv()
    const { POST } = await import(
      '@/app/api/bridge/v1/admin/enqueue/route'
    )
    const res = await POST(
      new Request(`http://x/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          environment_id,
          session_id: 'sess1',
          data: { prompt: 'hello' },
        }),
      }),
    )
    expect(res.status).toBe(200)
    const body = (await res.json()) as { work_id: string }
    expect(body.work_id).toBeTruthy()
  })

  test('full happy path: register → enqueue → poll → ack → heartbeat → events → archive → unregister', async () => {
    const reg = await registerEnv()
    const { environment_id, environment_secret } = reg

    // Enqueue via admin route
    const enq = await import('@/app/api/bridge/v1/admin/enqueue/route')
    const enqRes = await enq.POST(
      new Request(`http://x/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          environment_id,
          session_id: 'sessE',
          data: { prompt: 'do thing' },
        }),
      }),
    )
    const { work_id } = (await enqRes.json()) as { work_id: string }

    // Poll
    const pollMod = await import(
      '@/app/api/bridge/v1/environments/[envId]/work/poll/route'
    )
    const pollRes = await pollMod.GET(
      new Request(
        `http://x/api/bridge/v1/environments/${environment_id}/work/poll`,
        { headers: { Authorization: `Bearer ${environment_secret}` } },
      ),
      { params: Promise.resolve({ envId: environment_id }) },
    )
    const work = (await pollRes.json()) as { id: string; data: { prompt: string } }
    expect(work.id).toBe(work_id)
    expect(work.data.prompt).toBe('do thing')

    // Ack
    const ackMod = await import(
      '@/app/api/bridge/v1/environments/[envId]/work/[workId]/ack/route'
    )
    const ackRes = await ackMod.POST(
      new Request(`http://x/`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${environment_secret}` },
      }),
      { params: Promise.resolve({ envId: environment_id, workId: work_id }) },
    )
    expect(ackRes.status).toBe(204)

    // Heartbeat
    const hbMod = await import(
      '@/app/api/bridge/v1/environments/[envId]/work/[workId]/heartbeat/route'
    )
    const hbRes = await hbMod.POST(
      new Request(`http://x/`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${environment_secret}`,
        },
        body: JSON.stringify({}),
      }),
      { params: Promise.resolve({ envId: environment_id, workId: work_id }) },
    )
    const hb = (await hbRes.json()) as { lease_extended: boolean }
    expect(hb.lease_extended).toBe(true)

    // Session event
    const evMod = await import(
      '@/app/api/bridge/v1/sessions/[sessionId]/events/route'
    )
    const evRes = await evMod.POST(
      new Request(`http://x/`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${environment_secret}`,
        },
        body: JSON.stringify({
          events: [{ type: 'permission_response', granted: true }],
        }),
      }),
      { params: Promise.resolve({ sessionId: 'sessE' }) },
    )
    expect(evRes.status).toBe(204)

    // Archive
    const arMod = await import(
      '@/app/api/bridge/v1/sessions/[sessionId]/archive/route'
    )
    const arRes = await arMod.POST(
      new Request(`http://x/`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${environment_secret}`,
        },
        body: '{}',
      }),
      { params: Promise.resolve({ sessionId: 'sessE' }) },
    )
    expect(arRes.status).toBe(204)

    // Unregister
    const unregMod = await import(
      '@/app/api/bridge/v1/environments/bridge/[envId]/route'
    )
    const unregRes = await unregMod.DELETE(
      new Request(`http://x/`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${environment_secret}` },
      }),
      { params: Promise.resolve({ envId: environment_id }) },
    )
    expect(unregRes.status).toBe(204)
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

Expected: 2 new fails (admin enqueue route missing).

- [ ] **Step 3: Implement admin enqueue route**

Create `src/web/src/app/api/bridge/v1/admin/enqueue/route.ts`:

```typescript
import { NextResponse } from 'next/server'
import { getStore } from '@/lib/bridge/db'
import { enqueueWork, findEnvironment } from '@/lib/bridge/store'
import { emitWorkAvailable } from '@/lib/bridge/events'
import { bridgeError } from '@/lib/bridge/errors'

export async function POST(req: Request): Promise<NextResponse> {
  const body = (await req.json().catch(() => null)) as {
    environment_id?: string
    session_id?: string
    data?: unknown
  } | null
  if (
    !body ||
    typeof body.environment_id !== 'string' ||
    typeof body.session_id !== 'string'
  ) {
    return bridgeError(
      400,
      'invalid_request',
      'environment_id and session_id required',
    )
  }
  const store = getStore()
  if (!findEnvironment(store, body.environment_id)) {
    return bridgeError(404, 'not_found', 'Environment not found')
  }
  const work = enqueueWork(store, body.environment_id, {
    session_id: body.session_id,
    data: body.data ?? {},
  })
  emitWorkAvailable(body.environment_id)
  return NextResponse.json({ work_id: work.id }, { status: 200 })
}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd src/web && npx vitest run tests/bridge/integration.test.ts 2>&1 | tail -10
```

Expected: 17 integration tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/web/src/app/api/bridge/v1/admin/ src/web/tests/bridge/integration.test.ts
git commit -m "feat(web-bridge): admin enqueue route + full E2E happy-path test"
```

---

## Task 10: Final full-suite verification

**Files:** none modified.

- [ ] **Step 1: Run all bridge tests together**

```bash
cd src/web && npx vitest run tests/bridge/ 2>&1 | tail -10
```

Expected: 32 passes (12 store + 3 auth + 3 events + 17 integration + 2 errors). Adjust as math dictates — exact count matters less than 0 fails.

- [ ] **Step 2: Run full src/web test suite**

```bash
cd src/web && npm test 2>&1 | tail -10
```

Expected: all existing + new tests pass.

- [ ] **Step 3: Smoke-test against the running dev server**

If `next dev` is running on `127.0.0.1:3000`, hit register from curl:

```bash
curl -s -X POST http://127.0.0.1:3000/api/bridge/v1/environments/bridge \
  -H 'Content-Type: application/json' \
  -d '{"machine_name":"smoke","directory":"/tmp","max_sessions":1,"metadata":{"worker_type":"jarvis"}}'
```

Expected: `{ environment_id, environment_secret }`.

Then poll (with the secret echoed back):

```bash
ENV_ID=<from-prev>
SECRET=<from-prev>
time curl -s "http://127.0.0.1:3000/api/bridge/v1/environments/$ENV_ID/work/poll" \
  -H "Authorization: Bearer $SECRET"
```

Expected: returns `null` after about 25 seconds. Or substitute the env var `BRIDGE_POLL_TIMEOUT_MS=2000` to speed it up.

- [ ] **Step 4: Push the branch**

```bash
cd /home/ulrich/Documents/Projects/jarvis && git push 2>&1 | tail -5
```

---

## What we built

| Surface | Output |
|:--|:--|
| Lib | `lib/bridge/{store,auth,events,types,errors,db}.ts` (~600 lines) |
| Routes | 9 API routes under `app/api/bridge/v1/` (+ 1 admin enqueue) |
| Tests | 32 (vitest): 12 store unit, 3 auth unit, 3 events unit, 2 errors unit, 17 integration |
| Storage | `~/.jarvis/bridge.db` (SQLite via better-sqlite3) |
| Behavior | Self-contained CCR-compatible bridge target on loopback. The CLI's existing `bridgeApi.ts` client can speak to it once sub-project 2 wires the base URL + secret. |
