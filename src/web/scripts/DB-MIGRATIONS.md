# DB migrations: push vs. migrate (and the baseline trap)

The web app's Postgres schema (the `web` schema) is defined in
`src/lib/db/schema.ts` and versioned as SQL under `drizzle/`. There are **two
mutually exclusive** ways to get that schema into a database, and mixing them is
the trap.

## The two modes

| Mode | Command | Records in `drizzle.__drizzle_migrations`? | Use for |
|---|---|---|---|
| **push** | `drizzle-kit push` | **No** | fast local dev — diffs `schema.ts` straight onto the DB |
| **migrate** | `npm run db:migrate` | **Yes** — one row per `drizzle/*.sql` | shared / prod DBs with an auditable history |

`drizzle-kit push` compares `schema.ts` to the live DB and applies the diff. It
never writes the `drizzle.__drizzle_migrations` ledger. `npm run db:migrate`
replays the numbered `drizzle/NNNN_*.sql` files **in journal order** and records
each one it applies.

## The trap

Most long-lived JARVIS dev/prod DBs were bootstrapped with `push` (or an early
`drizzle-kit push` before the migration files existed). Their `web` schema is
fully present, but `drizzle.__drizzle_migrations` is **empty**. So the first time
you run `npm run db:migrate` on such a DB, the migrator sees zero applied
migrations and tries to run `0000_glorious_reavers.sql` — a pile of
`CREATE TABLE …` against tables that already exist — and aborts with
`relation "…" already exists`.

## The fix: baseline once

`npm run db:baseline` inserts one ledger row per journal entry
(`hash = sha256(migration file)`, `created_at = journal "when"`) **without
running any migration DDL**. After that, `npm run db:migrate` sees every existing
migration as applied and runs nothing — until a genuinely new migration is added.

```bash
# one-time, per already-provisioned DB:
DATABASE_URL=postgres://…  npm run db:baseline      # or add --dry-run to preview
# thereafter, normal flow:
DATABASE_URL=postgres://…  npm run db:migrate
```

`db:baseline` **refuses** if `drizzle.__drizzle_migrations` already has rows (that
means the DB is already migrate-managed and re-inserting would corrupt the
ledger). It touches only that one ledger table — never the `web` schema or your
data — and runs in a transaction.

## Which mode should I use?

- **Fresh local dev DB, throwaway:** `drizzle-kit push` is fine and fastest.
- **A DB you'll carry forward (shared dev, staging, prod):** use `migrate`. If it
  was pushed first, run `db:baseline` **once** to adopt it into the ledger, then
  only ever `migrate` it. Don't `push` a migrate-managed DB afterward — the two
  ledgers will diverge.

## Adding a new migration

1. Edit `src/lib/db/schema.ts`.
2. `drizzle-kit generate` → writes a new `drizzle/NNNN_*.sql` + updates the
   journal.
3. `npm run db:migrate` applies it and records it. (A baselined DB picks it up
   here — baseline covers only the migrations that existed at baseline time.)
