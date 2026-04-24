import { drizzle } from "drizzle-orm/postgres-js";
import postgres from "postgres";
import * as schema from "./schema";

const connectionString = process.env.DATABASE_URL;

export const persistenceEnabled = Boolean(connectionString);

const globalForDb = globalThis as unknown as { pg?: ReturnType<typeof postgres> };

const client = connectionString
  ? (globalForDb.pg ?? postgres(connectionString, { prepare: false, max: 10 }))
  : null;

if (process.env.NODE_ENV !== "production" && client) globalForDb.pg = client;

export const db = client ? drizzle(client, { schema }) : null;
export { schema };
