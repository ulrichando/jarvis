import { randomUUID } from "node:crypto";
import { eq, ne } from "drizzle-orm";
import { betterAuth } from "better-auth";
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
