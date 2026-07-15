import "server-only";

import { sql } from "drizzle-orm";
import { db } from "./index";

// Idempotent additive-column ensures for the `web` schema. The web DB is
// drizzle-push managed with no runtime migration flow, and the compose
// db-init SQL only runs on a FRESH Postgres data dir — so an EXISTING
// production DB won't get a newly-added column on deploy, and the
// conversations API would 500 on `SELECT ... kind`. Run the additive ALTER
// once at boot; `ADD COLUMN IF NOT EXISTS` with a constant default is a fast
// metadata-only op in Postgres, and the flag makes it a no-op after the
// first call. Add future additive columns here rather than a migration file.
let ensured = false;

export async function ensureWebSchema(): Promise<void> {
  if (!db || ensured) return;
  ensured = true;
  try {
    await db.execute(
      sql`ALTER TABLE web.conversations ADD COLUMN IF NOT EXISTS kind text NOT NULL DEFAULT 'chat'`,
    );
    // One continuous 'voice' conversation per user — backs the find-or-create
    // in /api/voice-memory so a concurrent racer can't insert a duplicate.
    await db.execute(
      sql`CREATE UNIQUE INDEX IF NOT EXISTS conversations_voice_user_uniq ON web.conversations (user_id) WHERE kind = 'voice'`,
    );
  } catch (err) {
    // Never let a schema-ensure failure take down the route — log and move on.
    // (Re-arm so a transient error retries on the next call.)
    ensured = false;
    console.error("[db] ensureWebSchema failed:", err);
  }
}
