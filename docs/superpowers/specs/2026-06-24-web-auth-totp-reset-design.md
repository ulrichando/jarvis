# Web Authentication: Login Enforcement + TOTP-Authorized Password Reset

- **Date:** 2026-06-24
- **Status:** Approved design (brainstorming) → pending implementation plan
- **Scope:** `src/web` (Next.js web app, better-auth ^1.6.16). Touches the desktop tray "Open in Browser" flow only insofar as it opens the web app — no desktop code changes expected.

## 1. Problem

The web app silently auto-logs-in. `getUserId()` in `src/lib/auth-helpers.ts` falls back to a hardcoded `LOCAL_USER_ID` (`00000000-0000-0000-0000-000000000001`) whenever there is no session, so every request — including the desktop tray's **Open in Browser** — is treated as that user. There is no login prompt and no real auth boundary. The same app is deployable via the online stack (`docs/runbook/deploy-online.md`), where this is a security hole: anyone reaching it is "logged in" as the local user.

better-auth is already wired (email+password enabled, 30-day sessions, a `/login` page exists), but nothing enforces it.

## 2. Goals

1. Enforce real **email + password login everywhere** — local desktop and online. No silent `LOCAL_USER_ID`.
2. **Persistent session** with a **sliding 7-day idle** timeout and a **30-day absolute** cap.
3. **Single-user**: only the owner's account exists; **public signup disabled**.
4. **Forgot-password recovery without email**: a **TOTP authenticator app** authorizes a password reset, with **backup codes** and a **local CLI reset** as backstops.

## 3. Non-goals

- No email or SMS sending infrastructure (explicitly dropped by the user).
- No passwordless login. (TOTP is reset-only; per the [better-auth 2FA docs](https://better-auth.com/docs/plugins/2fa) TOTP cannot be a primary/sole login factor anyway.)
- No multi-user, open signup, social login, or email verification.

## 4. Requirements & Decisions (locked during brainstorming)

| # | Decision | Choice |
|---|---|---|
| D1 | Enforcement scope | Everywhere (local + online), persistent session; remove `LOCAL_USER_ID` fallback |
| D2 | User model | Single-user; public signup **locked** |
| D3 | Session timeout | Sliding **7-day idle**, **30-day** absolute cap |
| D4 | Forgot-password | **TOTP** (authenticator app) authorizes reset — **no email** |
| D5 | Recovery fallbacks | One-time **backup codes** → **local CLI** reset on the box |

## 5. Architecture

Five units, each independently testable.

### 5.1 Login (existing, minimal change)
better-auth `emailAndPassword` (already enabled). The `(auth)/login` page already exists. Verify it posts to better-auth and lands the session cookie; restyle only if broken.

### 5.2 Gate enforcement — *the load-bearing change*
- **`getUserId()`** (`auth-helpers.ts`): stop returning `LOCAL_USER_ID` on no-session. Return `null` (or throw a typed `Unauthenticated`). **35 callers** must be audited (§10): each either (a) is already behind the proxy page/API gate, or (b) must explicitly 401/redirect.
- **`src/proxy.ts`**: unauthenticated **page** request → `302 /login?next=<path>`; unauthenticated **`/api/*`** → `401`. Keep the existing host-allowlist + canonical-host cookie handling (defense in depth, already present).
- **Cutover safety**: a single env escape hatch (e.g. `JARVIS_WEB_AUTH_ENFORCED`, default `1`) so enforcement can be flipped off in an emergency without a redeploy. Not a per-host bypass — global.

### 5.3 Session — sliding 7-day idle + 30-day cap
- `session.expiresIn = 7d`, `session.updateAge = 1d` (better-auth refreshes the expiry on use → sliding idle). Today's value is `expiresIn=30d / updateAge=1d`.
- **30-day absolute cap**: better-auth's `expiresIn` is the *idle* window once `updateAge` refreshes it, so it does **not** cap total age. Add a session-validity hook (better-auth `session.hooks` or a check in `getUserId`) that rejects sessions whose `createdAt` is older than 30 days, forcing re-login. (Confirm exact hook API in the plan.)

### 5.4 Single-user / signup locked
- Disable the public sign-up endpoint: prefer a built-in (`emailAndPassword.disableSignUp` if present in 1.6.16) else block `POST /api/auth/sign-up/email` at the proxy. Confirm the exact lever in the plan.
- **Seed the account**: a one-time local CLI (`bin/jarvis-web-account` or `src/web/scripts/seed-account.ts`) that calls better-auth's server `signUpEmail` against the local DB to create the owner's account (email + chosen password). Run once on the box.

### 5.5 TOTP enrollment (better-auth `twoFactor` plugin)
- Add the `twoFactor()` plugin → new schema (`twoFactor` table: encrypted `secret`, `backupCodes`). Apply via **`drizzle-kit push` / `psql`** — `db:migrate` hangs on this push-managed DB (project memory).
- Enrollment lives in **Account → Security** settings: `enable()` → `totpURI` (render QR) + `backupCodes` (show once, force the user to save them); `verifyTotp()` once to arm it.
- We do **not** turn TOTP into a login second factor — it stays dormant for login and is used only by the reset endpoint (§5.6).

### 5.6 Forgot-password → TOTP reset (custom endpoint)
The stock better-auth reset is email-based and unused. Custom flow:
1. **`/forgot-password`**: enter email. Server responds **generically** ("if that account exists and has an authenticator, enter your code") — no user enumeration.
2. **Code step**: enter the 6-digit TOTP code **or** a backup code.
3. **Server verify**: load the user's `twoFactor` secret (decrypt with `BETTER_AUTH_SECRET`), verify the code (otplib, ±1 period window) or consume a single-use backup code. **Rate-limited** (e.g. 5 attempts / 15 min / account) to stop brute force.
4. On success → issue a **single-use, 10-min reset token** (stored in the better-auth `verification` table or a small `password_reset` table).
5. **`/reset-password?token=…`**: set a new password → better-auth updates the hash; invalidate the token + optionally revoke existing sessions.

### 5.7 Local CLI reset (ultimate backstop)
`bin/jarvis-web-account reset-password` (same script as the seed): directly set a new password for the owner's account via better-auth's server API on the box. Covers "lost phone **and** backup codes." Relies on shell access to the machine, which the owner has.

## 6. Data model

- **better-auth `twoFactor` schema** (new table) — applied via `drizzle-kit push` / `psql -f`, not `db:migrate`.
- **Reset token**: reuse the existing `verification` table if its shape fits, else a small `password_reset(token, user_id, expires_at, used_at)` table.
- **No new columns on `user`** unless the plugin requires `twoFactorEnabled`.

## 7. Flows

- **Login**: `/login` → email+password → better-auth session cookie → redirect to `next` or `/`.
- **Enrollment**: Settings → Security → `enable()` → scan QR + save backup codes → `verifyTotp()` → armed.
- **Reset**: `/forgot-password` (email) → code step (TOTP/backup) → verify (rate-limited) → reset token → `/reset-password` → new password → sessions revoked → `/login`.
- **CLI reset**: `bin/jarvis-web-account reset-password` on the box → new password.

## 8. Error handling & security

- **No user enumeration**: generic responses on `/forgot-password` regardless of whether the email exists / has TOTP.
- **Brute-force**: rate-limit the reset-verify endpoint per account + per IP; lock for a cooldown after N failures.
- **TOTP replay**: accept ±1 period; reject a code already used within its window.
- **Reset token**: single-use, 10-min TTL, invalidated on use; revoke active sessions on password change.
- **Cookies**: `httpOnly`, `sameSite=Lax`, `secure` when served over HTTPS (online).
- **Fallback removal blast radius** (§10) is the top correctness risk, not just security.
- Keep the proxy **host allowlist** + **canonical-host** logic (already present) as defense in depth.

## 9. Testing

- **Unit**: TOTP verify (valid / wrong / expired / replayed); backup code single-use; reset-token TTL + single-use; `getUserId()` returns `null` with no session; 30-day absolute-cap check.
- **Integration**: unauthenticated page → 302 `/login`; unauthenticated `/api/*` → 401; full reset flow end-to-end; signup endpoint → 403; sliding-idle refresh extends expiry, 30-day cap forces re-login.
- **Manual**: desktop **Open in Browser** now prompts login; enroll TOTP; reset password via authenticator; CLI reset.

## 10. Risks & open questions

- **R1 (highest): removing `LOCAL_USER_ID` touches 35 `getUserId()` callers + 28 `LOCAL_USER_ID` references.** Each caller must tolerate "no user." The proxy gate should stop most unauthenticated requests upstream, but every data route needs a defensive check. Requires a full audit in the plan.
- **R2: existing data ownership.** All current chats/workspaces are owned by `LOCAL_USER_ID`. On cutover, **reassign** that data to the seeded account (one-time SQL migration), or the owner logs in to an empty app.
- **R3: standalone TOTP verification.** better-auth's `verifyTotp` is a logged-in 2FA step; the reset flow has no session. Confirm we can verify a code from the stored secret out-of-band (decrypt + otplib) — the main implementation unknown.
- **R4: sliding + absolute cap.** Confirm better-auth exposes a session hook to enforce the 30-day absolute cap alongside `expiresIn`/`updateAge`.
- **R5: signup-disable lever.** Confirm `disableSignUp` exists in 1.6.16, else proxy-block the route.

## 11. Rollout

- **Env**: `BETTER_AUTH_SECRET` (exists, reused to encrypt TOTP secret); no new email creds. `JARVIS_WEB_AUTH_ENFORCED=1` escape hatch.
- **Migration**: (a) `drizzle-kit push` the `twoFactor` + reset-token schema; (b) seed the owner account; (c) reassign `LOCAL_USER_ID` data → the account; (d) flip enforcement on.
- **Order**: schema → seed → reassign → enable enforcement, so the owner never sees an empty or locked app mid-migration.
