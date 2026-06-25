# Web Auth: Login Enforcement + TOTP Password Reset — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the JARVIS web app enforce real email+password login everywhere (no silent `LOCAL_USER_ID`), with sliding 7-day/30-day sessions, locked signup, and a TOTP-authenticator password reset (no email) plus backup-code and local-CLI backstops.

**Architecture:** Build on the existing better-auth setup (`src/web/src/lib/auth.ts`). Remove the `getUserId()` → `LOCAL_USER_ID` fallback and make every data path require a real session; tighten the proxy gate to validate (not just detect) the session; add better-auth's `twoFactor` plugin for TOTP enrollment + backup codes; add a small custom "reset-via-TOTP" endpoint (the stock reset is email-only) using `otplib` against the stored secret; ship a `bin/jarvis-web-account` CLI for seeding + emergency reset.

**Tech Stack:** Next.js (custom build — read `node_modules/next/dist/docs/` before touching routing/middleware), better-auth ^1.6.16 + `two-factor` plugin, drizzle-orm over Postgres `web.*` schema (push-managed: use `drizzle-kit push`/`psql`, NOT `db:migrate` — it hangs), `otplib`, vitest (run from `src/web`).

**Spec:** `docs/superpowers/specs/2026-06-24-web-auth-totp-reset-design.md`

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `src/web/src/lib/auth.ts` | better-auth config: add `twoFactor()`, session 7d/30d, lock signup | Modify |
| `src/web/src/lib/db/schema.ts` | add `twoFactors` + `passwordResets` tables | Modify |
| `src/web/src/lib/auth-helpers.ts` | `getUserId` → nullable; add `requireUserId` | Modify |
| `src/web/src/lib/auth-totp.ts` | standalone TOTP/backup-code verify against stored secret | Create |
| `src/web/src/proxy.ts` | validate session in page gate; 401 unauth `/api/*`; block signup | Modify |
| `src/web/src/app/api/auth/reset/request/route.ts` | start reset: email → challenge (generic) | Create |
| `src/web/src/app/api/auth/reset/verify/route.ts` | verify TOTP/backup → single-use reset token (rate-limited) | Create |
| `src/web/src/app/api/auth/reset/complete/route.ts` | token + new password → update hash, revoke sessions | Create |
| `src/web/src/app/(auth)/forgot-password/page.tsx` | reset UI (email → code → new password) | Create |
| `src/web/src/components/settings/security.tsx` | TOTP enroll UI (QR + backup codes) | Create |
| `bin/jarvis-web-account` | CLI: `seed` + `reset-password` (local backstop) | Create |
| `src/web/tests/auth-*.test.ts` | unit + integration tests | Create |

**The 35 `getUserId()` callers** are handled in Task 4 by changing the shared helper, not each call site individually where possible.

---

## Task 1: Add `twoFactor` plugin, otplib, and schema

**Files:**
- Modify: `src/web/package.json` (add `otplib`)
- Modify: `src/web/src/lib/auth.ts:43-111`
- Modify: `src/web/src/lib/auth-client.ts`
- Modify: `src/web/src/lib/db/schema.ts` (append tables)

- [ ] **Step 1: Install otplib**

Run (from `src/web`): `npm install otplib@^12`
Expected: added to `dependencies`, lockfile updated.

- [ ] **Step 2: Add the twoFactor server plugin**

In `src/web/src/lib/auth.ts`, import and register the plugin (its `secret`/`backupCodes` are encrypted with `BETTER_AUTH_SECRET`):

```typescript
import { twoFactor } from "better-auth/plugins";
// ...inside betterAuth({ ... }) add:
  plugins: [
    twoFactor({
      issuer: "JARVIS",
      // We only use TOTP for password RESET, never as a login second factor,
      // so we don't enable the 2fa sign-in enforcement.
      totpOptions: { period: 30, digits: 6 },
    }),
  ],
```

- [ ] **Step 3: Add the twoFactor client plugin**

In `src/web/src/lib/auth-client.ts`:

```typescript
import { twoFactorClient } from "better-auth/client/plugins";
export const authClient = createAuthClient({ plugins: [twoFactorClient()] });
```

- [ ] **Step 4: Define the schema tables**

The `twoFactor` plugin needs a table; we also add a reset-token table. Append to `src/web/src/lib/db/schema.ts` (match the existing `uuid`/`pgTable` style; verify column names against `node_modules/better-auth/dist/plugins/two-factor` — the plugin expects `twoFactor(id, userId, secret, backupCodes)`):

```typescript
export const twoFactors = pgTable("two_factors", {
  id: uuid("id").primaryKey().defaultRandom(),
  userId: uuid("user_id").notNull().references(() => users.id, { onDelete: "cascade" }),
  secret: text("secret").notNull(),
  backupCodes: text("backup_codes").notNull(),
});

export const passwordResets = pgTable("password_resets", {
  id: uuid("id").primaryKey().defaultRandom(),
  userId: uuid("user_id").notNull().references(() => users.id, { onDelete: "cascade" }),
  token: text("token").notNull().unique(),
  expiresAt: timestamp("expires_at", { withTimezone: true }).notNull(),
  usedAt: timestamp("used_at", { withTimezone: true }),
});
```

Map `twoFactor: schema.twoFactors` in the `drizzleAdapter` schema block in `auth.ts`.

- [ ] **Step 5: Push the schema**

Run (from `src/web`): `npx drizzle-kit push`
Expected: `two_factors` + `password_resets` created. (Do NOT run `db:migrate` — it hangs on this push-managed DB.)

- [ ] **Step 6: Commit**

```bash
git add src/web/package.json src/web/package-lock.json src/web/src/lib/auth.ts src/web/src/lib/auth-client.ts src/web/src/lib/db/schema.ts
git commit -m "feat(web-auth): add twoFactor plugin + otplib + reset/2fa schema"
```

---

## Task 2: Session — sliding 7-day idle + 30-day absolute cap

**Files:**
- Modify: `src/web/src/lib/auth.ts:107-110`
- Test: `src/web/tests/auth-session-cap.test.ts`

- [ ] **Step 1: Write the failing test** (`src/web/tests/auth-session-cap.test.ts`)

```typescript
import { describe, it, expect } from "vitest";
import { isSessionWithinAbsoluteCap } from "@/lib/auth-helpers";

describe("session absolute cap", () => {
  it("accepts a session created 29 days ago", () => {
    const created = new Date(Date.now() - 29 * 864e5);
    expect(isSessionWithinAbsoluteCap(created)).toBe(true);
  });
  it("rejects a session created 31 days ago", () => {
    const created = new Date(Date.now() - 31 * 864e5);
    expect(isSessionWithinAbsoluteCap(created)).toBe(false);
  });
});
```

- [ ] **Step 2: Run it — expect FAIL** (`isSessionWithinAbsoluteCap` not exported)

Run: `npm test -- auth-session-cap`

- [ ] **Step 3: Set the sliding window + add the cap helper**

In `auth.ts` change `session` to a sliding 7-day idle window:

```typescript
  session: {
    expiresIn: 60 * 60 * 24 * 7,  // 7-day idle window (refreshed on use)
    updateAge: 60 * 60 * 24,      // refresh the expiry once a day of activity
  },
```

Add to `auth-helpers.ts`:

```typescript
const ABSOLUTE_CAP_MS = 30 * 864e5; // 30-day hard cap regardless of activity
export function isSessionWithinAbsoluteCap(createdAt: Date): boolean {
  return Date.now() - createdAt.getTime() < ABSOLUTE_CAP_MS;
}
```

- [ ] **Step 4: Enforce the cap in `getUserId`** (done in Task 3, which reads `session.session.createdAt` and returns null when the cap is exceeded). Note here; implement there.

- [ ] **Step 5: Run test — expect PASS**, then commit.

```bash
git add src/web/src/lib/auth.ts src/web/src/lib/auth-helpers.ts src/web/tests/auth-session-cap.test.ts
git commit -m "feat(web-auth): sliding 7-day idle session + 30-day absolute cap helper"
```

---

## Task 3: Remove the `LOCAL_USER_ID` fallback — `getUserId` becomes nullable

**Files:**
- Modify: `src/web/src/lib/auth-helpers.ts`
- Test: `src/web/tests/auth-getuserid.test.ts`

- [ ] **Step 1: Failing test** — `getUserId` returns `null` with no session, and a `requireUserId` throws `Unauthenticated`.

```typescript
import { describe, it, expect, vi } from "vitest";
// Mock auth.api.getSession to return null, assert getUserId(headers) === null.
// Mock it to return a 31-day-old session, assert getUserId === null (cap).
// Mock a fresh session, assert getUserId === that user id.
```

(Write the three cases with `vi.mock("@/lib/auth", …)`.)

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Rewrite `auth-helpers.ts`**

```typescript
import "server-only";
import { headers as nextHeaders } from "next/headers";
import { auth } from "./auth";

export class Unauthenticated extends Error {
  constructor() { super("Unauthenticated"); this.name = "Unauthenticated"; }
}

/** Logged-in user id, or null. No silent LOCAL_USER_ID fallback. */
export async function getUserId(reqHeaders?: Headers): Promise<string | null> {
  const session = await auth.api.getSession({
    headers: reqHeaders ?? (await nextHeaders()),
  });
  if (!session?.user?.id) return null;
  if (session.session?.createdAt &&
      !isSessionWithinAbsoluteCap(new Date(session.session.createdAt))) {
    return null; // past the 30-day absolute cap → force re-login
  }
  return session.user.id;
}

/** For API routes: the user id, or throw Unauthenticated (caller → 401). */
export async function requireUserId(reqHeaders?: Headers): Promise<string> {
  const id = await getUserId(reqHeaders);
  if (!id) throw new Unauthenticated();
  return id;
}
```

(Keep `isSessionWithinAbsoluteCap` from Task 2 in this file.)

- [ ] **Step 4: Run test — expect PASS.** Commit.

```bash
git add src/web/src/lib/auth-helpers.ts src/web/tests/auth-getuserid.test.ts
git commit -m "feat(web-auth): getUserId returns null (no LOCAL_USER_ID fallback) + requireUserId"
```

---

## Task 4: Make the 35 callers handle "no user"

**Files:** the 35 files from `grep -rln 'getUserId(' src/ | grep -v node_modules`.

- [ ] **Step 1: Enumerate + classify.**

Run: `grep -rln 'getUserId(' src/ | grep -v node_modules`
Classify each: **API route handler** → replace `const uid = await getUserId(...)` with `let uid; try { uid = await requireUserId(req.headers); } catch { return new Response("Unauthorized", { status: 401 }); }` (or a shared `withUser` wrapper). **Server component / page** → if `getUserId()` is null, `redirect("/login")` (next/navigation).

- [ ] **Step 2: Add a `withUser` API wrapper** (`src/web/src/lib/auth-route.ts`) to keep this DRY:

```typescript
import { requireUserId, Unauthenticated } from "./auth-helpers";
export async function withUser(
  req: Request, fn: (uid: string) => Promise<Response>,
): Promise<Response> {
  try { return await fn(await requireUserId(req.headers)); }
  catch (e) {
    if (e instanceof Unauthenticated) return new Response("Unauthorized", { status: 401 });
    throw e;
  }
}
```

- [ ] **Step 3: Convert each API caller** to `withUser`. **Server components** use `getUserId() ?? redirect("/login")`.

- [ ] **Step 4: Verify** the app still type-checks: `npx tsc --noEmit` (from `src/web`). Expected: no errors about `string | null`.

- [ ] **Step 5: Commit** in logical chunks (e.g. one commit per top-level route group).

---

## Task 5: Proxy gate — validate, don't just detect

**Files:**
- Modify: `src/web/src/proxy.ts:55-167`

- [ ] **Step 1:** The page gate (line 156-167) currently passes any request that has a `better-auth.session_token` cookie, even an expired/forged one. Keep the cheap cookie check as a fast negative (no cookie → redirect), but ALSO ensure stale cookies don't grant page access: rely on the server components calling `getUserId() ?? redirect("/login")` (Task 4) as the authoritative check. Document this two-layer model in a comment.

- [ ] **Step 2:** Ensure unauthenticated `/api/*` returns 401: confirm the bearer gate + the `withUser` wrapper cover it. The same-origin browser carve-out (line 190+) must additionally require a valid session — verify it calls into a session check, not just `Sec-Fetch-Site`.

- [ ] **Step 3: Manual check** — with no/stale cookie, hitting a page → `/login`; hitting `/api/conversations` → 401. Commit.

```bash
git add src/web/src/proxy.ts
git commit -m "fix(web-auth): proxy gate relies on validated session, not cookie presence"
```

---

## Task 6: Lock signup

**Files:**
- Modify: `src/web/src/lib/auth.ts` (emailAndPassword), `src/web/src/proxy.ts` (block route), remove `/signup` from `LOGIN_PUBLIC_PREFIXES`.

- [ ] **Step 1:** Set `emailAndPassword.disableSignUp: true` if supported in 1.6.16 (grep `node_modules/better-auth/dist/.../email-password` for the option); else block `POST /api/auth/sign-up/email` in the proxy with 403.
- [ ] **Step 2:** Remove `'/signup'` from `LOGIN_PUBLIC_PREFIXES` in `proxy.ts` (no public signup page).
- [ ] **Step 3: Test** — `POST /api/auth/sign-up/email` → 403. Commit.

---

## Task 7: TOTP enrollment UI (Account → Security)

**Files:**
- Create: `src/web/src/components/settings/security.tsx`
- Wire into the existing settings page.

- [ ] **Step 1:** Component flow using the client plugin:
  - `await authClient.twoFactor.enable({ password })` → returns `{ totpURI, backupCodes }`.
  - Render the `totpURI` as a QR (use existing QR dep or `qrcode`); show `backupCodes` once with a "I saved these" confirm.
  - `await authClient.twoFactor.verifyTotp({ code })` to arm it.
- [ ] **Step 2:** Show enrolled/not-enrolled state; allow `generateBackupCodes()` to regenerate.
- [ ] **Step 3: Manual test** — enroll with Google Authenticator, see codes. Commit.

---

## Task 8: Standalone TOTP/backup verify + reset endpoints

**Files:**
- Create: `src/web/src/lib/auth-totp.ts`
- Create: `src/web/src/app/api/auth/reset/{request,verify,complete}/route.ts`
- Test: `src/web/tests/auth-totp.test.ts`

- [ ] **Step 1: Spike (concrete action):** read `node_modules/better-auth/dist/plugins/two-factor/*` to confirm how `secret` is stored (plaintext base32 vs encrypted with `BETTER_AUTH_SECRET`). If encrypted, reuse better-auth's `symmetricDecrypt` (exported from `better-auth/crypto`) before handing to otplib.

- [ ] **Step 2: Failing test** for `verifyTotpForUser` and `consumeBackupCode`:

```typescript
import { authenticator } from "otplib";
// Seed a two_factors row with a known secret; assert verifyTotpForUser(userId,
// authenticator.generate(secret)) === true, wrong code === false, and that a
// backup code verifies once then is consumed (second use === false).
```

- [ ] **Step 3: Implement `auth-totp.ts`:**

```typescript
import { authenticator } from "otplib";
import { db, schema } from "./db";
import { eq } from "drizzle-orm";
// decrypt secret if Step 1 found it encrypted
export async function verifyTotpForUser(userId: string, code: string): Promise<boolean> {
  const row = await db.select().from(schema.twoFactors)
    .where(eq(schema.twoFactors.userId, userId)).then(r => r[0]);
  if (!row) return false;
  const secret = /* decrypt(row.secret) per Step 1 */ row.secret;
  return authenticator.verify({ token: code, secret });
}
export async function consumeBackupCode(userId: string, code: string): Promise<boolean> {
  // load row.backupCodes (decrypt), check membership, remove on match, persist.
}
```

- [ ] **Step 4: `reset/request`** — body `{ email }`; always return `{ ok: true }` (generic). If the user exists AND has a `two_factors` row, set a short server-side challenge marker; otherwise no-op. No enumeration.

- [ ] **Step 5: `reset/verify`** — body `{ email, code }`; **rate-limit** (5/15min per email+IP — reuse any existing limiter or a simple table/in-memory map). Verify via `verifyTotpForUser` OR `consumeBackupCode`; on success insert a `password_resets` row (`token = randomUUID()`, `expiresAt = now+10min`) and return `{ token }`.

- [ ] **Step 6: `reset/complete`** — body `{ token, password }`; look up an unused, unexpired `password_resets`; update the password via better-auth's server API (`auth.api.setPassword` / context password hasher — confirm in the spike); mark token used; revoke the user's sessions (`auth.api.revokeSessions`).

- [ ] **Step 7: Run tests — PASS.** Commit.

```bash
git add src/web/src/lib/auth-totp.ts "src/web/src/app/api/auth/reset/**" src/web/tests/auth-totp.test.ts
git commit -m "feat(web-auth): TOTP/backup-code reset endpoints (request/verify/complete)"
```

---

## Task 9: Forgot-password UI

**Files:**
- Create: `src/web/src/app/(auth)/forgot-password/page.tsx`
- Modify: `src/web/src/app/(auth)/login/page.tsx` (add "Forgot password?" link)
- Modify: `proxy.ts` `LOGIN_PUBLIC_PREFIXES` → add `/forgot-password`

- [ ] **Step 1:** Three-step client form: (1) email → `POST /api/auth/reset/request`; (2) 6-digit code → `POST /api/auth/reset/verify` → token; (3) new password → `POST /api/auth/reset/complete` → redirect `/login`. Generic messaging throughout.
- [ ] **Step 2:** Add `/forgot-password` to `LOGIN_PUBLIC_PREFIXES`.
- [ ] **Step 3: Manual test** the full reset. Commit.

---

## Task 10: `bin/jarvis-web-account` CLI (seed + emergency reset)

**Files:**
- Create: `bin/jarvis-web-account`

- [ ] **Step 1:** A Node/tsx script run on the box that loads `src/web` env and calls better-auth's server API:
  - `seed --email <e> --password <p>`: `auth.api.signUpEmail(...)` (triggers the existing data-reassign hook).
  - `reset-password --email <e> --password <p>`: set the password directly (same server API as Task 8 Step 6).
- [ ] **Step 2:** `chmod +x`; document in `docs/runbook/`. Commit.

---

## Task 11: Cutover

- [ ] **Step 1:** Confirm the `databaseHooks.user.create.after` reassignment (auth.ts:88-103) covers all `LOCAL_USER_ID`-owned tables. `artifacts` is owned via `conversationId` cascade (no direct `userId`) — OK. Grep the other 28 `LOCAL_USER_ID` refs for any direct-owned table not in the hook; add it if found.
- [ ] **Step 2:** Seed the owner account (Task 10) → verifies data reassignment.
- [ ] **Step 3:** Invalidate existing sessions so the next "Open in Browser" prompts login: `DELETE FROM web.sessions;` (psql).
- [ ] **Step 4:** Enroll TOTP (Task 7). Save backup codes.
- [ ] **Step 5:** Restart the web dev/prod server; open browser → expect `/login`.

---

## Task 12: Verification

- [ ] Run full web test suite from `src/web`: `npm test` — expect green (incl. the new auth tests).
- [ ] `npx tsc --noEmit` clean.
- [ ] Manual: Open in Browser → `/login`; log in; idle behavior; forgot-password via authenticator; backup code; CLI reset; signup → 403.

---

## Self-Review notes

- **Spec coverage:** D1 (Tasks 3-5), D2 (Tasks 6,10), D3 (Task 2), D4 (Tasks 7-9), D5 (Tasks 8-10). R1 (Task 4), R2 (Task 11), R3 (Task 8 Step 1 spike), R4 (Task 2), R5 (Task 6) all have owning tasks.
- **Open spikes (concrete, not placeholders):** Task 8 Step 1 (secret encryption), Task 6 Step 1 (`disableSignUp` lever), Task 8 Step 6 (`setPassword` API) — each is a read-the-source action with a defined fallback.
- **Type consistency:** `getUserId(): Promise<string | null>`, `requireUserId(): Promise<string>`, `isSessionWithinAbsoluteCap(Date): boolean`, `verifyTotpForUser(userId, code)`, `consumeBackupCode(userId, code)` used consistently across tasks.
