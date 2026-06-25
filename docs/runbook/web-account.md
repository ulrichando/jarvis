# Web Account Management — `jarvis-web-account`

The web app has **public signup disabled** (the proxy 403s every `POST /api/auth/sign-up*`).
The single owner account must be provisioned on the box via this CLI, and the same CLI
handles emergency password recovery without a session.

---

## Prerequisites

- `src/web/.env.local` must contain `DATABASE_URL` and `BETTER_AUTH_SECRET`.
- `npm install` has been run in `src/web/` (provides `tsx`).
- The Postgres database is reachable (run the db-init script if fresh: `src/web/scripts/db-init/01-create-schema.sql`).

---

## Seed the owner account

Run **once** when setting up a new box or after wiping the database:

```bash
bin/jarvis-web-account seed \
  --email owner@example.com \
  --password 'MyS3cur3P@ss!'
```

Optional `--name` sets the display name (defaults to the email local-part):

```bash
bin/jarvis-web-account seed \
  --email owner@example.com \
  --password 'MyS3cur3P@ss!' \
  --name 'Ulrich'
```

**What happens:**

1. Calls `auth.api.signUpEmail` in-process (bypasses the proxy block — no HTTP hop).
2. Fires the `databaseHooks.user.create.after` hook in `src/lib/auth.ts`:
   if this is the **first real (non-local) user**, all existing conversations and
   projects owned by `LOCAL_USER_ID` (`00000000-0000-0000-0000-000000000001`) are
   reassigned to the new account. This is the intended single-user self-hosted
   upgrade path.
3. Prints the new user ID.

Exits non-zero if an account already exists for that email (better-auth returns
a duplicate-user error).

---

## Emergency password reset (lost authenticator AND backup codes)

If the owner is **locked out** (forgot password, and has no working TOTP device
or backup codes), reset the password directly on the box:

```bash
bin/jarvis-web-account reset-password \
  --email owner@example.com \
  --password 'NewS3cur3P@ss!'
```

**What happens:**

1. Looks up the user by email in the database.
2. Hashes the new password with scrypt (`better-auth/crypto::hashPassword`) —
   exactly the format better-auth's credential sign-in verifier expects.
3. Updates the `credential` account row (creates it if absent — supports
   accounts that were OAuth-only and are adding a password for the first time).
4. **Revokes all active sessions** for that user, so any stolen/stale cookie is
   immediately invalid.

The user can then sign in at `/login` with the new password.
If TOTP was previously enrolled, TOTP verification is still required at login
(the 2FA enrollment is separate from the password). To bypass TOTP (full lockout),
the user will need to use the TOTP backup codes, or if those are also lost, an
operator must delete the `web.two_factors` row for that user directly in Postgres:

```sql
DELETE FROM web.two_factors WHERE user_id = '<user-uuid>';
UPDATE web.users SET two_factor_enabled = false WHERE id = '<user-uuid>';
```

---

## Usage reference

```
bin/jarvis-web-account --help
```

```
Options:
  --email      User email address (required)
  --password   Password (min 8 chars) (required)
  --name       Display name for seed (optional, defaults to email local-part)
  --env        Path to .env file (default: src/web/.env.local)
```

Custom env file (e.g. for production):

```bash
bin/jarvis-web-account seed \
  --env src/web/.env.production \
  --email owner@example.com \
  --password 'MyS3cur3P@ss!'
```
