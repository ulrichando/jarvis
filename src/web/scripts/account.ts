#!/usr/bin/env tsx
/**
 * JARVIS web account management CLI.
 *
 * Usage:
 *   npx tsx scripts/account.ts seed           --email <e> --password <p> [--name <n>]
 *   npx tsx scripts/account.ts reset-password --email <e> --password <p>
 *
 * Run via the wrapper: bin/jarvis-web-account (from the project root)
 *
 * Env: reads src/web/.env.local by default; override with --env <path>.
 * Requires DATABASE_URL and BETTER_AUTH_SECRET to be set.
 *
 * IMPORTANT: seed triggers the databaseHooks.user.create.after hook in
 * auth.ts, which reassigns LOCAL_USER_ID conversations/projects to the new
 * account when this is the first real (non-local) user. That data migration is
 * intentional on single-user self-hosted boxes upgrading to logins.
 *
 * Design note on import ordering:
 *   auth.ts throws at module-evaluation time if DATABASE_URL is unset, so all
 *   app imports are done lazily (dynamic import) AFTER env is loaded by
 *   loadEnv(). Static imports here are limited to Node builtins that read no
 *   env vars at evaluation time.
 */

import { resolve, dirname } from "node:path";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";

// ---------------------------------------------------------------------------
// Arg parsing — runs before env loading (reads only process.argv, no env)
// ---------------------------------------------------------------------------
function parseArgs(argv: string[]): {
  command: string;
  email: string;
  password: string;
  name: string;
  envFile: string | null;
} {
  const args = argv.slice(2); // strip "node" + script path
  const get = (flag: string): string | undefined => {
    const i = args.indexOf(flag);
    return i !== -1 ? args[i + 1] : undefined;
  };

  const command = args[0] ?? "";
  const email = get("--email") ?? "";
  const password = get("--password") ?? "";
  const name = get("--name") ?? "";
  const envFile = get("--env") ?? null;

  return { command, email, password, name, envFile };
}

function printUsage(): void {
  console.log(`
JARVIS web account CLI

Usage:
  jarvis-web-account seed           --email <email> --password <password> [--name <name>]
  jarvis-web-account reset-password --email <email> --password <password>

Options:
  --email      User email address (required)
  --password   Password (min 8 chars) (required)
  --name       Display name for seed (optional, defaults to email local-part)
  --env        Path to .env file (default: src/web/.env.local)

Commands:
  seed            Create the owner account via better-auth's in-process server
                  API (bypasses the HTTP proxy block on public signup).
                  Triggers the LOCAL_USER_ID data migration: the first real
                  user inherits existing conversations and projects.
                  Exits non-zero if an account already exists for that email.

  reset-password  Emergency: set a new password for an existing account WITHOUT
                  a session (for the "lost authenticator AND backup codes" case).
                  Also revokes all active sessions for that user.

Examples:
  jarvis-web-account seed --email owner@example.com --password 'MyS3cur3P@ss!'
  jarvis-web-account reset-password --email owner@example.com --password 'NewP@ss2026'
`.trim());
}

// ---------------------------------------------------------------------------
// Env loading — must run before any app module import (auth.ts throws at eval
// time if DATABASE_URL is absent).
// ---------------------------------------------------------------------------
function loadEnv(envPath: string): void {
  if (!existsSync(envPath)) {
    console.error(`[account] env file not found: ${envPath}`);
    process.exit(1);
  }
  // process.loadEnvFile (Node ≥ 20.6) reads KEY=VALUE lines and sets missing
  // keys — does NOT override existing env vars set by the parent shell.
  process.loadEnvFile(envPath);
}

// ---------------------------------------------------------------------------
// Command: seed
// ---------------------------------------------------------------------------
async function cmdSeed(
  email: string,
  password: string,
  name: string,
): Promise<void> {
  if (!email || !password) {
    console.error("[account] --email and --password are required for seed");
    process.exit(1);
  }
  if (password.length < 8) {
    console.error("[account] password must be at least 8 characters");
    process.exit(1);
  }

  const displayName = name || email.split("@")[0];

  // Dynamic import AFTER env is loaded — auth.ts evaluates DATABASE_URL here.
  const { auth } = await import("../src/lib/auth.js");

  console.log(`[account] Creating account: ${email} (name: ${displayName})`);

  // Call better-auth's IN-PROCESS server API — no HTTP hop, no CSRF check,
  // bypasses the proxy block on POST /api/auth/sign-up*. Fires the
  // databaseHooks.user.create.after hook in auth.ts (data migration).
  const result = await auth.api.signUpEmail({
    body: {
      email,
      password,
      name: displayName,
    },
  });

  if (!result?.user?.id) {
    console.error(
      "[account] sign-up returned no user — unexpected response:",
      result,
    );
    process.exit(1);
  }

  console.log(`[account] Account created successfully.`);
  console.log(`[account] User ID: ${result.user.id}`);
  console.log(`[account] Email:   ${result.user.email}`);
}

// ---------------------------------------------------------------------------
// Command: reset-password
// ---------------------------------------------------------------------------
async function cmdResetPassword(
  email: string,
  password: string,
): Promise<void> {
  if (!email || !password) {
    console.error(
      "[account] --email and --password are required for reset-password",
    );
    process.exit(1);
  }
  if (password.length < 8) {
    console.error("[account] password must be at least 8 characters");
    process.exit(1);
  }

  // Dynamic imports AFTER env is loaded.
  const { db, schema } = await import("../src/lib/db/index.js");
  const { setCredentialPassword } = await import(
    "../src/lib/auth-password.js"
  );
  const { eq } = await import("drizzle-orm");

  if (!db) {
    console.error("[account] DATABASE_URL is not set — cannot connect");
    process.exit(1);
  }

  // Resolve user by email.
  const [user] = await db
    .select({ id: schema.users.id, email: schema.users.email })
    .from(schema.users)
    .where(eq(schema.users.email, email))
    .limit(1);

  if (!user) {
    console.error(`[account] No account found for email: ${email}`);
    process.exit(1);
  }

  console.log(`[account] Resetting password for: ${email} (id: ${user.id})`);

  // Shared helper: hash + upsert credential row + revoke sessions.
  await setCredentialPassword(user.id, password);

  console.log(`[account] Password reset successfully.`);
  console.log(
    `[account] All active sessions for ${email} have been revoked.`,
  );
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------
async function main(): Promise<void> {
  const { command, email, password, name, envFile } = parseArgs(process.argv);

  // ── Handle no-arg / help before env loading ────────────────────────────
  if (!command || command === "--help" || command === "-h") {
    printUsage();
    process.exit(0);
  }

  // ── Load env (must precede any app module import) ──────────────────────
  // import.meta.dirname is not polyfilled by tsx; derive from import.meta.url.
  // This file lives at scripts/account.ts; go up one level to reach src/web.
  const __scriptDir = dirname(fileURLToPath(import.meta.url));
  const webRoot = resolve(__scriptDir, "..");
  const resolvedEnv = envFile
    ? resolve(envFile)
    : resolve(webRoot, ".env.local");

  loadEnv(resolvedEnv);

  // ── Dispatch ────────────────────────────────────────────────────────────
  switch (command) {
    case "seed":
      await cmdSeed(email, password, name);
      break;

    case "reset-password":
      await cmdResetPassword(email, password);
      break;

    default:
      console.error(`[account] Unknown command: ${command}`);
      console.error(
        '  Use "seed" or "reset-password". Run with no args for help.',
      );
      process.exit(1);
  }

  process.exit(0);
}

main().catch((err: unknown) => {
  console.error(
    "[account] Fatal error:",
    err instanceof Error ? err.message : err,
  );
  process.exit(1);
});
