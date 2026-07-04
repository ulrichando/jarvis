import { sql } from "drizzle-orm";
import { db, schema } from "@/lib/db";
import { requireUserId, Unauthenticated } from "@/lib/auth-helpers";
import { LOCAL_USER_ID } from "@/lib/chat/persist";
import { loadSettings } from "@/lib/settings/store";
import {
  summarizeUsage,
  type UsageRow,
  type UsageResponse,
} from "@/lib/usage";

export const runtime = "nodejs";

export async function GET(req: Request) {
  let userId: string;
  try {
    userId = await requireUserId(req.headers);
  } catch (e) {
    if (e instanceof Unauthenticated)
      return new Response("Unauthorized", { status: 401 });
    throw e;
  }

  let rows: UsageRow[] = [];
  if (db) {
    // Single-user box: rows written while auth was disabled belong to the
    // LOCAL_USER_ID fallback identity — same human, so include them.
    // Timestamps are `timestamp without time zone` in the PG session's local
    // wall-clock; comparing against date_trunc(now()) keeps both sides in
    // the same clock, so day/month windows are correct.
    const result = await db.execute(sql`
      SELECT
        model,
        count(*)::int                       AS "requests",
        coalesce(sum(tokens_in), 0)::int    AS "tokensIn",
        coalesce(sum(tokens_out), 0)::int   AS "tokensOut",
        (count(*) FILTER (WHERE created_at >= date_trunc('day', now())))::int
          AS "requestsToday",
        coalesce(sum(tokens_in) FILTER (WHERE created_at >= date_trunc('day', now())), 0)::int
          AS "tokensInToday",
        coalesce(sum(tokens_out) FILTER (WHERE created_at >= date_trunc('day', now())), 0)::int
          AS "tokensOutToday"
      FROM ${schema.usageEvents}
      WHERE user_id IN (${userId}, ${LOCAL_USER_ID})
        AND created_at >= date_trunc('month', now())
      GROUP BY model
    `);
    rows = result as unknown as UsageRow[];
  }

  const summary = summarizeUsage(rows);
  const settings = await loadSettings();
  const monthlyUsd = settings.usage.monthlyBudgetUsd ?? null;

  const body: UsageResponse = {
    ...summary,
    budget: {
      monthlyUsd,
      percentUsed: monthlyUsd ? summary.month.costUsd / monthlyUsd : null,
      alertsEnabled: settings.usage.costAlerts,
    },
    generatedAt: new Date().toISOString(),
  };
  return Response.json(body);
}
