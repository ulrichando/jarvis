#!/usr/bin/env node
// Baseline an already-provisioned Postgres so `npm run db:migrate` treats the
// existing schema as fully applied instead of replaying 0000 into live tables.
//
// WHY THIS EXISTS
// ---------------
// Long-lived dev/prod DBs got their `web` schema from `drizzle-kit push`, which
// applies schema diffs WITHOUT recording anything in `drizzle.__drizzle_migrations`
// (that ledger is only written by `drizzle-kit migrate` / the postgres-js
// migrator). So on such a DB the ledger is empty, and `db:migrate` sees zero
// applied migrations → tries to run 0000_glorious_reavers.sql (CREATE TABLE …)
// against tables that already exist → aborts with "relation already exists".
//
// This script inserts one ledger row per journal entry (hash = sha256 of the
// migration file, created_at = the journal's `when`), exactly as the migrator
// would have on a clean run — see node_modules/drizzle-orm/pg-core/dialect.js
// (migrate()) and node_modules/drizzle-orm/migrator.js (readMigrationFiles()).
// The migrator's skip guard is `lastDbMigration.created_at < migration.when`,
// so with every row present it applies nothing.
//
// SAFETY
// ------
// - REFUSES if drizzle.__drizzle_migrations already has ANY rows. Baselining is
//   only for the push-managed → migrate-managed transition; a populated ledger
//   means migrate is already the source of truth and re-inserting would corrupt
//   it. Re-run only after manually clearing the ledger if you know why it's dirty.
// - Touches ONLY drizzle.__drizzle_migrations (CREATE SCHEMA/TABLE IF NOT EXISTS
//   + INSERT). It never runs a migration's DDL and never touches the `web`
//   schema or your data. If you actually need to CHANGE tables, use `db:migrate`
//   (migrate-managed) or `drizzle-kit push` (push-managed) — never both.
// - Wrapped in a transaction; a mid-run failure rolls back cleanly.
//
// USAGE
// -----
//   DATABASE_URL=postgres://…  node scripts/db-baseline.mjs [--dry-run]
//
// See scripts/DB-MIGRATIONS.md for the push-vs-migrate decision.

import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import postgres from "postgres";

const MIGRATIONS_SCHEMA = "drizzle";
const MIGRATIONS_TABLE = "__drizzle_migrations";

const __here = path.dirname(fileURLToPath(import.meta.url));
const migrationsFolder = path.resolve(__here, "..", "drizzle");
const dryRun = process.argv.includes("--dry-run");

function fail(msg) {
  console.error(`db-baseline: ${msg}`);
  process.exit(1);
}

// Read journal + compute per-migration hashes the same way the drizzle migrator
// does (sha256 over the raw file contents; created_at = journal entry `when`).
function readMigrationLedger() {
  const journalPath = path.join(migrationsFolder, "meta", "_journal.json");
  if (!fs.existsSync(journalPath)) {
    fail(`can't find ${journalPath}`);
  }
  const journal = JSON.parse(fs.readFileSync(journalPath, "utf8"));
  const entries = Array.isArray(journal.entries) ? journal.entries : [];
  if (entries.length === 0) {
    fail("journal has no entries — nothing to baseline");
  }
  return entries.map((entry) => {
    const sqlPath = path.join(migrationsFolder, `${entry.tag}.sql`);
    if (!fs.existsSync(sqlPath)) {
      fail(`journal references ${entry.tag}.sql but it is missing`);
    }
    const contents = fs.readFileSync(sqlPath, "utf8");
    return {
      tag: entry.tag,
      hash: crypto.createHash("sha256").update(contents).digest("hex"),
      when: entry.when,
    };
  });
}

async function main() {
  // Journal read + hashing is offline, so --dry-run works with no DATABASE_URL.
  const rows = readMigrationLedger();
  console.log(`db-baseline: ${rows.length} journal entrie(s) to record:`);
  for (const r of rows) {
    console.log(`  ${r.tag}  sha256=${r.hash.slice(0, 12)}…  when=${r.when}`);
  }

  if (dryRun) {
    console.log("db-baseline: --dry-run, no DB connection made.");
    return;
  }

  const connectionString = process.env.DATABASE_URL;
  if (!connectionString) {
    fail("DATABASE_URL is not set");
  }

  // prepare:false matches src/lib/db/index.ts (pgbouncer-safe).
  const sql = postgres(connectionString, { prepare: false, max: 1 });
  try {
    await sql.begin(async (tx) => {
      await tx.unsafe(
        `CREATE SCHEMA IF NOT EXISTS "${MIGRATIONS_SCHEMA}"`,
      );
      await tx.unsafe(
        `CREATE TABLE IF NOT EXISTS "${MIGRATIONS_SCHEMA}"."${MIGRATIONS_TABLE}" (
           id SERIAL PRIMARY KEY,
           hash text NOT NULL,
           created_at bigint
         )`,
      );

      const [{ count }] = await tx.unsafe(
        `SELECT count(*)::int AS count FROM "${MIGRATIONS_SCHEMA}"."${MIGRATIONS_TABLE}"`,
      );
      if (count > 0) {
        // Throw to roll back the CREATE-IF-NOT-EXISTS statements above.
        throw new Error(
          `${MIGRATIONS_SCHEMA}.${MIGRATIONS_TABLE} already has ${count} row(s) — ` +
            `this DB is already migrate-managed. Refusing to baseline (would ` +
            `duplicate the ledger). Clear the table manually only if you know why.`,
        );
      }

      for (const r of rows) {
        await tx.unsafe(
          `INSERT INTO "${MIGRATIONS_SCHEMA}"."${MIGRATIONS_TABLE}" ("hash", "created_at")
             VALUES ($1, $2)`,
          [r.hash, r.when],
        );
      }
    });
    console.log(
      `db-baseline: recorded ${rows.length} migration(s). ` +
        `\`npm run db:migrate\` will now apply nothing until a NEW migration is added.`,
    );
  } catch (err) {
    fail(err instanceof Error ? err.message : String(err));
  } finally {
    await sql.end({ timeout: 5 });
  }
}

main().catch((err) => fail(err instanceof Error ? err.message : String(err)));
