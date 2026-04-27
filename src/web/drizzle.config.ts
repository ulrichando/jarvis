import type { Config } from "drizzle-kit";

export default {
  schema: "./src/lib/db/schema.ts",
  out: "./drizzle",
  dialect: "postgresql",
  dbCredentials: {
    url:
      process.env.DATABASE_URL ??
      "postgres://postgres:postgres@localhost:5432/jarvis_web",
  },
  // Only manage the `web` schema. The shared `jarvis` database also
  // has a `public.conversations` (JARVIS memory system, ~5K rows,
  // totally different shape) that drizzle would otherwise want to
  // drop on push because our schema.ts doesn't declare it.
  schemaFilter: ["web"],
  strict: true,
  verbose: true,
} satisfies Config;
