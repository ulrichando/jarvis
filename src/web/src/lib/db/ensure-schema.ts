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
//
// Concurrency: memoize the in-flight DDL as a single promise. A bare boolean
// set BEFORE the await let a second concurrent cold-start caller sail past the
// guard and reference change_seq before its column existed (→ 500). Every
// caller now awaits the SAME completion; on failure the promise is re-armed so
// a transient error retries on the next call.
let ensuredPromise: Promise<void> | null = null;

export function ensureWebSchema(): Promise<void> {
  if (!db) return Promise.resolve();
  if (ensuredPromise) return ensuredPromise;
  ensuredPromise = runEnsureWebSchema().catch((err) => {
    ensuredPromise = null;
    console.error("[db] ensureWebSchema failed:", err);
  });
  return ensuredPromise;
}

async function runEnsureWebSchema(): Promise<void> {
  if (!db) return;
  await db.execute(
    sql`ALTER TABLE web.conversations ADD COLUMN IF NOT EXISTS kind text NOT NULL DEFAULT 'chat'`,
  );
  // Two-way mobile sync pull cursor: a monotonic per-write sequence on
  // conversations. Sequence + column + default, backfill existing rows, then
  // a unique index (doubles as the pull ordering key). All additive/idempotent.
  // Ordered BEFORE the voice index so a data-dependent index failure can't
  // block the change_seq DDL (which would 500 every sync route).
  await db.execute(sql`CREATE SEQUENCE IF NOT EXISTS web.conversation_change_seq`);
  await db.execute(
    sql`ALTER TABLE web.conversations ADD COLUMN IF NOT EXISTS change_seq bigint`,
  );
  await db.execute(
    sql`ALTER TABLE web.conversations ALTER COLUMN change_seq SET DEFAULT nextval('web.conversation_change_seq')`,
  );
  await db.execute(
    sql`UPDATE web.conversations SET change_seq = nextval('web.conversation_change_seq') WHERE change_seq IS NULL OR change_seq = 0`,
  );
  await db.execute(
    sql`CREATE UNIQUE INDEX IF NOT EXISTS conversations_change_seq_idx ON web.conversations (change_seq)`,
  );
  // Ordering support for the mobile message-pull slice (created_at).
  await db.execute(
    sql`CREATE INDEX IF NOT EXISTS messages_conv_created_idx ON web.messages (conversation_id, created_at)`,
  );
  // Soft-delete tombstones for two-way delete sync. Null = live. A deleted
  // conversation is still returned by the pull (as a tombstone) so other
  // devices remove it; deleted messages are excluded from the message pull.
  await db.execute(sql`ALTER TABLE web.conversations ADD COLUMN IF NOT EXISTS deleted_at timestamp`);
  await db.execute(sql`ALTER TABLE web.messages ADD COLUMN IF NOT EXISTS deleted_at timestamp`);
  // One continuous 'voice' conversation per user — backs the find-or-create
  // in /api/voice-memory so a concurrent racer can't insert a duplicate.
  await db.execute(
    sql`CREATE UNIQUE INDEX IF NOT EXISTS conversations_voice_user_uniq ON web.conversations (user_id) WHERE kind = 'voice'`,
  );
  // Curated memory stores (user/memory/procedure) for /api/memory — the
  // cloud port of the local voice agent's file-backed USER.md/MEMORY.md/
  // PROCEDURES.md. One row per (user_id, kind); the unique index backs the
  // upsert-then-SELECT-FOR-UPDATE read-modify-write in the route.
  await db.execute(
    sql`CREATE TABLE IF NOT EXISTS web.curated_memories (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      user_id uuid NOT NULL REFERENCES web.users(id) ON DELETE CASCADE,
      kind text NOT NULL,
      entries jsonb NOT NULL DEFAULT '[]',
      version integer NOT NULL DEFAULT 1,
      updated_at timestamp NOT NULL DEFAULT now(),
      created_at timestamp NOT NULL DEFAULT now()
    )`,
  );
  await db.execute(
    sql`CREATE UNIQUE INDEX IF NOT EXISTS curated_memories_user_kind_uniq ON web.curated_memories (user_id, kind)`,
  );
  // Chat memory (the model-callable `memory` tool) — one row per saved user
  // fact, injected into every chat system prompt (src/lib/chat/memory.ts).
  // Distinct from curated_memories above.
  await db.execute(
    sql`CREATE TABLE IF NOT EXISTS web.user_memories (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      user_id uuid NOT NULL REFERENCES web.users(id) ON DELETE CASCADE,
      content text NOT NULL,
      created_at timestamp NOT NULL DEFAULT now()
    )`,
  );
  await db.execute(
    sql`CREATE INDEX IF NOT EXISTS user_memories_user_idx ON web.user_memories (user_id)`,
  );
}
