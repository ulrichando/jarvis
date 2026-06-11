import { randomUUID } from "node:crypto";
import { betterAuth } from "better-auth";
import { drizzleAdapter } from "better-auth/adapters/drizzle";
import { db, schema } from "./db";

// JARVIS web login. better-auth over the existing web.* Postgres schema —
// users/sessions/accounts/verifications already match better-auth's shape.
// Email + password; sessions are server-side rows fronted by an httpOnly
// cookie. The drizzle adapter maps better-auth's singular model names onto
// our plural tables.
if (!db) {
  throw new Error("DATABASE_URL is required for JARVIS login (better-auth).");
}

export const auth = betterAuth({
  baseURL: process.env.BETTER_AUTH_URL ?? "http://localhost:3000",
  secret: process.env.BETTER_AUTH_SECRET,
  database: drizzleAdapter(db, {
    provider: "pg",
    schema: {
      user: schema.users,
      session: schema.sessions,
      account: schema.accounts,
      verification: schema.verifications,
    },
  }),
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
  session: {
    expiresIn: 60 * 60 * 24 * 30, // 30 days
    updateAge: 60 * 60 * 24, // refresh once a day
  },
});

export type Session = typeof auth.$Infer.Session;
