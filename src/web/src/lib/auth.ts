import { randomUUID } from "node:crypto";
import { eq, ne } from "drizzle-orm";
import { betterAuth } from "better-auth";
import { twoFactor } from "better-auth/plugins";
import { drizzleAdapter } from "better-auth/adapters/drizzle";
import { db, schema } from "./db";
import { LOCAL_USER_ID } from "./chat/persist";

// JARVIS web login. better-auth over the existing web.* Postgres schema —
// users/sessions/accounts/verifications already match better-auth's shape.
// Email + password; sessions are server-side rows fronted by an httpOnly
// cookie. The drizzle adapter maps better-auth's singular model names onto
// our plural tables.
if (!db) {
  throw new Error("DATABASE_URL is required for JARVIS login (better-auth).");
}

/**
 * Self-hosted: the app is reached by whatever name this box answers to —
 * localhost, 127.0.0.1, a LAN IP, a single-label hostname, or *.local. By
 * default better-auth only trusts BETTER_AUTH_URL's origin, so signing in
 * from http://127.0.0.1:3000 (or a LAN address) failed the CSRF origin check
 * with 403 INVALID_ORIGIN. We additionally trust a same-origin request
 * (Origin host equals the Host header) when that host is private-network
 * shaped. Public DNS names stay untrusted so a DNS-rebinding page can't pass
 * the check. Reverse-proxy / extra origins: set BETTER_AUTH_TRUSTED_ORIGINS
 * (comma-separated; read natively by better-auth).
 */
function isPrivateHost(hostname: string): boolean {
  const h = hostname.toLowerCase().replace(/^\[|\]$/g, "");
  if (h === "localhost" || h === "::1" || h.endsWith(".localhost")) return true;
  if (/^127\./.test(h)) return true;
  if (/^(10\.|192\.168\.|169\.254\.)/.test(h)) return true;
  if (/^172\.(1[6-9]|2[0-9]|3[01])\./.test(h)) return true;
  if (!h.includes(".")) return true; // single-label intranet name (e.g. "moon")
  return (
    h.endsWith(".local") ||
    h.endsWith(".lan") ||
    h.endsWith(".internal") ||
    h.endsWith(".home.arpa")
  );
}

export const auth = betterAuth({
  baseURL: process.env.BETTER_AUTH_URL ?? "http://localhost:3000",
  secret: process.env.BETTER_AUTH_SECRET,
  trustedOrigins: async (request?: Request) => {
    const origin = request?.headers.get("origin");
    const host =
      request?.headers.get("x-forwarded-host") ?? request?.headers.get("host");
    if (!origin || !host) return [];
    try {
      const parsed = new URL(origin);
      if (parsed.host === host && isPrivateHost(parsed.hostname)) {
        return [origin];
      }
    } catch {
      /* malformed Origin header — not trusted */
    }
    return [];
  },
  database: drizzleAdapter(db, {
    provider: "pg",
    schema: {
      user: schema.users,
      session: schema.sessions,
      account: schema.accounts,
      verification: schema.verifications,
      // twoFactor plugin accesses this under the "twoFactor" model name.
      twoFactor: schema.twoFactors,
    },
  }),
  plugins: [
    twoFactor({
      issuer: "JARVIS",
      totpOptions: { period: 30, digits: 6 },
    }),
  ],
  emailAndPassword: {
    enabled: true,
    autoSignIn: true,
    minPasswordLength: 8,
  },
  advanced: {
    database: {
      // Our id columns are Postgres `uuid`; better-auth's default string IDs
      // would fail the insert. Emit real UUIDs instead.
      generateId: () => randomUUID(),
    },
  },
  databaseHooks: {
    user: {
      create: {
        // The FIRST real (non-local) account inherits the existing local-user
        // data (chats + projects) — a single-user self-hosted box upgrading to
        // logins. Subsequent accounts start fresh.
        after: async (user: { id: string }) => {
          if (!db || user.id === LOCAL_USER_ID) return;
          const realUsers = await db
            .select({ id: schema.users.id })
            .from(schema.users)
            .where(ne(schema.users.id, LOCAL_USER_ID));
          if (realUsers.length !== 1) return; // not the first real user
          await db
            .update(schema.conversations)
            .set({ userId: user.id })
            .where(eq(schema.conversations.userId, LOCAL_USER_ID));
          await db
            .update(schema.projects)
            .set({ userId: user.id })
            .where(eq(schema.projects.userId, LOCAL_USER_ID));
        },
      },
    },
  },
  session: {
    expiresIn: 60 * 60 * 24 * 30, // 30 days
    updateAge: 60 * 60 * 24, // refresh once a day
  },
});

export type Session = typeof auth.$Infer.Session;
