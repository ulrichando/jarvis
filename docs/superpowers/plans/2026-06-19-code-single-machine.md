# /code Single Machine + Ephemeral Sandboxes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `/code` show exactly one canonical local machine plus separately-grouped ephemeral cloud sandboxes (auto-GC'd), instead of an ever-growing flat list of "machines."

**Architecture:** Three `src/web`-only changes — (1) the local-machine identity in `lib/bridge/store.ts` keys on `(user, machine_name)` and ignores `directory`/containers, so one box = one row; (2) a lazy TTL reaper deletes stale container rows on `GET /environments` and annotates the machine `online/offline`; (3) `/code` UI splits the list into one machine + cloud sandboxes. No CLI change, no schema migration.

**Tech Stack:** TypeScript, Next.js (App Router) route handlers, `better-sqlite3` (synchronous), vitest.

**Spec:** `docs/superpowers/specs/2026-06-19-code-single-machine-design.md`

---

## File Structure

- **Modify** `src/web/src/lib/bridge/store.ts` — change `findEnvironmentByIdentity` (drop `directory`, scope to non-container); add `SANDBOX_TTL_MS`/`ONLINE_TTL_MS` consts, `isEnvironmentOnline()`, `reapStaleSandboxes()`.
- **Modify** `src/web/src/app/api/bridge/v1/environments/route.ts` — GET reaps stale sandboxes + annotates `online`.
- **Modify** `src/web/src/app/(app)/code/[[...session]]/page.tsx` — `Machine` type gains `online`; derive one machine + sandbox list; render split with status dot.
- **Create** `src/web/tests/bridge/environments-machine.test.ts` — unit tests for identity, reaper, online, and the GET route.

> ⚠️ Before Task 1, confirm the `environments/cloud` route dedups containers via its own per-repo path (NOT `findEnvironmentByIdentity`) — there are currently 2 container rows sharing `machine_name='Cloud container'` + `directory='/workspace'`, proving it does. Task 1's scoping relies on this.

---

### Task 1: Machine identity — one row per machine

**Files:**
- Modify: `src/web/src/lib/bridge/store.ts` (`findEnvironmentByIdentity` ~line 447; its caller in `createEnvironment` ~line 341)
- Test: `src/web/tests/bridge/environments-machine.test.ts`

- [ ] **Step 1: Write the failing test**

```ts
import { describe, expect, test, beforeEach } from 'vitest'
import { _resetForTests, getStore } from '@/lib/bridge/db'
import { createEnvironment, listEnvironments } from '@/lib/bridge/store'

const USER = '00000000-0000-0000-0000-000000000001'
beforeEach(() => { _resetForTests() })

describe('machine identity', () => {
  test('same machine, two directories → one row', () => {
    const store = getStore()
    const a = createEnvironment(store, {
      machine_name: 'Moon', directory: '/repo/a', max_sessions: 4,
      worker_type: 'claude_code_repl', user_id: USER,
    })
    const b = createEnvironment(store, {
      machine_name: 'Moon', directory: '/repo/b', max_sessions: 4,
      worker_type: 'claude_code_repl', user_id: USER,
    })
    expect(b.environment_id).toBe(a.environment_id)
    expect(listEnvironments(store, USER).filter(e => e.worker_type !== 'container'))
      .toHaveLength(1)
  })

  test('two containers stay separate', () => {
    const store = getStore()
    const a = createEnvironment(store, {
      machine_name: 'Cloud container', directory: '/workspace', max_sessions: 4,
      worker_type: 'container', user_id: USER,
    })
    const b = createEnvironment(store, {
      machine_name: 'Cloud container', directory: '/workspace', max_sessions: 4,
      worker_type: 'container', user_id: USER,
    })
    expect(b.environment_id).not.toBe(a.environment_id)
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/web && npx vitest run tests/bridge/environments-machine.test.ts -t "machine identity"`
Expected: FAIL — "same machine, two directories" gets two different ids (current identity includes `directory`).

- [ ] **Step 3: Implement — drop `directory`, scope to non-container**

In `store.ts`, change `findEnvironmentByIdentity` to:

```ts
export function findEnvironmentByIdentity(
  store: Store,
  userId: string | null,
  machineName: string,
): EnvironmentRow | null {
  // A machine = (owner, machine_name). Directory is a mutable facet, not
  // identity, so the same box attaching from a different folder reuses its
  // row. Scoped to non-container so cloud sandboxes (which share
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
```

Then update the caller in `createEnvironment` (~line 341) to drop the `directory` arg:

```ts
  const existing =
    (input.reuse_id ? findEnvironment(store, input.reuse_id) : null) ??
    findEnvironmentByIdentity(store, input.user_id ?? null, input.machine_name)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/web && npx vitest run tests/bridge/environments-machine.test.ts -t "machine identity"`
Expected: PASS (both tests).

- [ ] **Step 5: Commit**

```bash
git add src/web/src/lib/bridge/store.ts src/web/tests/bridge/environments-machine.test.ts
git commit -m "fix(web/code): machine identity keys on (user, machine_name), not directory"
```

---

### Task 2: TTL reaper + online helper

**Files:**
- Modify: `src/web/src/lib/bridge/store.ts` (add consts + two functions, near `deleteEnvironment` ~line 474)
- Test: `src/web/tests/bridge/environments-machine.test.ts` (append)

- [ ] **Step 1: Write the failing test**

```ts
import {
  createEnvironment, listEnvironments, reapStaleSandboxes,
  isEnvironmentOnline, SANDBOX_TTL_MS, ONLINE_TTL_MS,
} from '@/lib/bridge/store'
import { createSession } from '@/lib/bridge/store' // adjust import if session helper name differs

describe('reaper + online', () => {
  test('reaps stale container, keeps machine + fresh sandbox + active-session sandbox', () => {
    const store = getStore()
    const now = Date.now()
    const stale = createEnvironment(store, { machine_name: 'Cloud container', directory: '/workspace', max_sessions: 4, worker_type: 'container', user_id: USER })
    const fresh = createEnvironment(store, { machine_name: 'Cloud container', directory: '/workspace', max_sessions: 4, worker_type: 'container', user_id: USER })
    const busy  = createEnvironment(store, { machine_name: 'Cloud container', directory: '/workspace', max_sessions: 4, worker_type: 'container', user_id: USER })
    const machine = createEnvironment(store, { machine_name: 'Moon', directory: '/repo', max_sessions: 4, worker_type: 'claude_code_repl', user_id: USER })

    // age `stale` and `busy` past the TTL; give `busy` an active session
    store.db.prepare('UPDATE environments SET last_seen_at = ? WHERE environment_id IN (?, ?)')
      .run(now - SANDBOX_TTL_MS - 1000, stale.environment_id, busy.environment_id)
    store.db.prepare(
      `INSERT INTO sessions (session_id, environment_id, archived, created_at) VALUES (?, ?, 0, ?)`
    ).run('s_busy', busy.environment_id, now)

    const reaped = reapStaleSandboxes(store, now)
    expect(reaped).toBe(1) // only `stale`
    const ids = listEnvironments(store, USER).map(e => e.environment_id)
    expect(ids).not.toContain(stale.environment_id)
    expect(ids).toContain(fresh.environment_id)
    expect(ids).toContain(busy.environment_id)   // spared: active session
    expect(ids).toContain(machine.environment_id) // never reaped (not container)
  })

  test('isEnvironmentOnline reflects last_seen', () => {
    const now = Date.now()
    const base = { environment_id: 'e', environment_secret: 's', machine_name: 'Moon', directory: '/r', branch: null, git_repo_url: null, max_sessions: 4, worker_type: 'claude_code_repl', user_id: USER, created_at: now, config_json: null }
    expect(isEnvironmentOnline({ ...base, last_seen_at: now - 1000 }, now)).toBe(true)
    expect(isEnvironmentOnline({ ...base, last_seen_at: now - ONLINE_TTL_MS - 1000 }, now)).toBe(false)
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/web && npx vitest run tests/bridge/environments-machine.test.ts -t "reaper"`
Expected: FAIL — `reapStaleSandboxes`/`isEnvironmentOnline`/consts not exported.

- [ ] **Step 3: Implement in `store.ts`** (add after `deleteEnvironment`)

```ts
/** A local machine is "online" if its heartbeat landed within this window. */
export const ONLINE_TTL_MS = 2 * 60 * 1000
/** A container sandbox with no activity for this long is reaped. */
export const SANDBOX_TTL_MS = 24 * 60 * 60 * 1000

export function isEnvironmentOnline(
  row: EnvironmentRow,
  now: number = Date.now(),
): boolean {
  return now - row.last_seen_at < ONLINE_TTL_MS
}

/**
 * Delete container (cloud sandbox) rows idle past SANDBOX_TTL_MS with no
 * active session. Best-effort GC run lazily on GET /environments — the
 * container itself was already reaped on archive; this clears the dangling
 * row. Machine rows (non-container) are never deleted here. Returns the count.
 */
export function reapStaleSandboxes(
  store: Store,
  now: number = Date.now(),
): number {
  const cutoff = now - SANDBOX_TTL_MS
  const stale = store.db
    .prepare(
      `SELECT environment_id FROM environments
       WHERE worker_type = 'container' AND last_seen_at < ?`,
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/web && npx vitest run tests/bridge/environments-machine.test.ts`
Expected: PASS (all tests, both describes).

- [ ] **Step 5: Commit**

```bash
git add src/web/src/lib/bridge/store.ts src/web/tests/bridge/environments-machine.test.ts
git commit -m "feat(web/code): TTL reaper for stale cloud sandboxes + online helper"
```

---

### Task 3: Wire reaper + online into GET /environments

**Files:**
- Modify: `src/web/src/app/api/bridge/v1/environments/route.ts`
- Test: `src/web/tests/bridge/environments-machine.test.ts` (append)

- [ ] **Step 1: Write the failing test**

```ts
import { vi } from 'vitest'
vi.mock('@/lib/auth-helpers', () => ({
  getUserId: async () => '00000000-0000-0000-0000-000000000001',
}))

describe('GET /environments', () => {
  test('reaps stale sandbox + returns online flag', async () => {
    const store = getStore()
    const now = Date.now()
    const stale = createEnvironment(store, { machine_name: 'Cloud container', directory: '/workspace', max_sessions: 4, worker_type: 'container', user_id: USER })
    createEnvironment(store, { machine_name: 'Moon', directory: '/repo', max_sessions: 4, worker_type: 'claude_code_repl', user_id: USER })
    store.db.prepare('UPDATE environments SET last_seen_at = ? WHERE environment_id = ?')
      .run(now - SANDBOX_TTL_MS - 1000, stale.environment_id)

    const { GET } = await import('@/app/api/bridge/v1/environments/route')
    const res = await GET(new Request('http://127.0.0.1:3000/api/bridge/v1/environments'))
    const body = (await res.json()) as { environments: Array<{ machine_name: string; online: boolean; worker_type: string }> }

    expect(body.environments).toHaveLength(1)           // stale sandbox reaped
    expect(body.environments[0].machine_name).toBe('Moon')
    expect(body.environments[0].online).toBe(true)      // just created → online
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/web && npx vitest run tests/bridge/environments-machine.test.ts -t "GET /environments"`
Expected: FAIL — stale sandbox still listed (no reaper) and `online` undefined.

- [ ] **Step 3: Implement — edit `route.ts` GET**

Replace the body of `GET` (keep the try/catch + error handling) with:

```ts
  try {
    const store = getStore()
    const userId = await getUserId(req.headers)
    reapStaleSandboxes(store) // lazy GC of stale cloud sandboxes
    const now = Date.now()
    const environments = listEnvironments(store, userId).map((e) => ({
      environment_id: e.environment_id,
      machine_name: e.machine_name,
      directory: e.directory,
      branch: e.branch,
      git_repo_url: e.git_repo_url,
      max_sessions: e.max_sessions,
      worker_type: e.worker_type,
      created_at: e.created_at,
      last_seen_at: e.last_seen_at,
      online: isEnvironmentOnline(e, now),
    }))
    return NextResponse.json({ environments })
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    return bridgeError(500, 'internal_error', `DB error: ${msg}`)
  }
```

And update the import line:

```ts
import { listEnvironments, reapStaleSandboxes, isEnvironmentOnline } from '@/lib/bridge/store'
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/web && npx vitest run tests/bridge/environments-machine.test.ts`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add src/web/src/app/api/bridge/v1/environments/route.ts src/web/tests/bridge/environments-machine.test.ts
git commit -m "feat(web/code): GET /environments reaps stale sandboxes + marks machine online"
```

---

### Task 4: `/code` UI — one machine + sandbox group

**Files:**
- Modify: `src/web/src/app/(app)/code/[[...session]]/page.tsx`

No unit test (presentational); verified by build + manual check.

- [ ] **Step 1: Extend the `Machine` type** (~line 19) — add `online`:

```ts
type Machine = {
  environment_id: string;
  machine_name: string;
  directory: string;
  branch: string | null;
  git_repo_url: string | null;
  worker_type: string;
  last_seen_at: number;
  online: boolean;
};
```

- [ ] **Step 2: Derive machine vs sandboxes + fix auto-select**

Where `machines` is consumed (the fetch handler ~line 167-171 sets `machines`; selection logic uses `selected`), add derived values in the component body (after `const [machines, ...]` / `const [selected, ...]`):

```ts
// Exactly one local machine (non-container); containers are cloud sandboxes.
const machine = (machines ?? []).find((m) => m.worker_type !== "container") ?? null;
const sandboxes = (machines ?? []).filter((m) => m.worker_type === "container");
```

Change the auto-select (line ~171) to prefer the machine:

```ts
        setMachines(j.environments);
        setSelected((cur) => cur ?? machineOf(j.environments));
```

where `machineOf` is a tiny module-scope helper above the component:

```ts
function machineOf(envs: Machine[]): Machine | null {
  return envs.find((m) => m.worker_type !== "container") ?? null;
}
```

- [ ] **Step 3: Render the split**

In the machine-picker region of the JSX, render `machine` and `sandboxes` as two labeled groups. Machine row shows a status dot driven by `machine.online`; each sandbox shows its repo + an `expired`/idle hint. Minimal shape:

```tsx
{machine && (
  <button
    type="button"
    onClick={() => setSelected(machine)}
    className="flex items-center gap-2 rounded-md px-2 py-1 text-sm"
  >
    <span
      className={`size-2 rounded-full ${machine.online ? "bg-emerald-500" : "bg-muted-foreground/40"}`}
      title={machine.online ? "online" : "offline"}
    />
    <span className="font-medium">{machine.machine_name}</span>
    <span className="text-xs text-muted-foreground">{machine.online ? "online" : "offline"}</span>
  </button>
)}
{sandboxes.length > 0 && (
  <div className="mt-2">
    <p className="px-2 text-[11px] uppercase tracking-wide text-muted-foreground">Cloud sandboxes</p>
    {sandboxes.map((s) => (
      <button key={s.environment_id} type="button" onClick={() => setSelected(s)}
        className="flex items-center gap-2 rounded-md px-2 py-1 text-sm">
        <span className="size-2 rounded-full bg-sky-500/60" />
        <span className="truncate">{s.git_repo_url ?? s.machine_name}</span>
      </button>
    ))}
  </div>
)}
```

Adapt class names / placement to the existing picker markup (match surrounding components). Do NOT remove existing dispatch logic that branches on `selected?.worker_type`.

- [ ] **Step 4: Build to verify**

Run: `cd src/web && npx tsc --noEmit && npx vitest run`
Expected: tsc 0 errors; full web suite green (including the new bridge tests).

- [ ] **Step 5: Manual check**

Restart the dev server if needed (`rm -rf .next` if turbopack is stale), hard-refresh `/code`. Expected: exactly one machine row with a green dot (online), no stale container rows; if a cloud sandbox is live it appears under "Cloud sandboxes."

- [ ] **Step 6: Commit**

```bash
git add "src/web/src/app/(app)/code/[[...session]]/page.tsx"
git commit -m "feat(web/code): split UI into one machine + cloud sandboxes with online dot"
```

---

## Self-Review

- **Spec coverage:** identity (Task 1), reaper + TTLs + online (Task 2), route wiring (Task 3), UI split + migration-via-reaper (Task 4), tests (Tasks 1-3). All spec sections covered. The "Future" items (env templates, local-agent self-exit) are intentionally out of scope.
- **Type consistency:** `reapStaleSandboxes(store, now?)`, `isEnvironmentOnline(row, now?)`, `ONLINE_TTL_MS`, `SANDBOX_TTL_MS`, `findEnvironmentByIdentity(store, userId, machineName)` — names match across tasks. `Machine.online: boolean` matches the route's `online` field.
- **Open risk:** Task 1 assumes `environments/cloud` does not rely on `findEnvironmentByIdentity` for container dedup (verified by the two existing same-identity container rows). The pre-Task-1 check + the "two containers stay separate" test guard this.
</content>
