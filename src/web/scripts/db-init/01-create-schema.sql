-- Runs once on a fresh Postgres data dir (postgres image executes everything in
-- /docker-entrypoint-initdb.d/ as POSTGRES_USER on first init). The drizzle
-- migrations create tables in the `web` schema (e.g. "web"."accounts" in
-- 0000_*.sql) but never CREATE the schema itself — long-lived dev DBs got it
-- from an early `drizzle-kit push`. Without this, `npm run db:migrate` on a
-- fresh container aborts with: schema "web" does not exist.
CREATE SCHEMA IF NOT EXISTS web;
