import { NextResponse } from "next/server";
import { promises as fs } from "node:fs";
import path from "node:path";
import { workspaceRoot } from "@/lib/workspace/storage";

export const runtime = "nodejs";

/**
 * GET /api/workspace/[id]/analytics
 *
 * Parses the workspace's dev.log (written by every `boltAction
 * type="start"` action — same file the user can `tail` themselves)
 * for HTTP request lines. Aggregates: top routes, status code
 * distribution, recent errors. Cheap, no external dependencies, no
 * client-side instrumentation needed.
 *
 * What this is:
 *   - A development-mode signal of "what's happening when I poke at
 *     my running app" — easy debugging surface for `Settings →
 *     Analytics` without having to deploy first.
 *
 * What this is NOT:
 *   - Real production analytics. For that you need page-load events
 *     from real users, which requires a deployed app + edge ingestion.
 *     That's V2 — see the StubSection's "needs" list.
 *
 * Log line patterns we recognize (best-effort, framework-agnostic):
 *   - Next.js dev:     ` GET / 200 in 47ms`     (with leading whitespace)
 *   - Vite:            `  GET /api/foo 200 1ms`
 *   - Express morgan:  `GET /foo 200 -`
 *   - Generic:         `<METHOD> <PATH> <STATUS> ...`
 */

type LogLine = {
  method: string;
  path: string;
  status: number;
  ms?: number;
};

const HTTP_LINE = /\b(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(\/[^\s?]*)\S*\s+(\d{3})(?:.*?(\d+)\s*ms)?/i;

function parseLine(line: string): LogLine | null {
  const m = line.match(HTTP_LINE);
  if (!m) return null;
  const method = m[1].toUpperCase();
  const p = m[2];
  const status = parseInt(m[3], 10);
  if (!Number.isFinite(status) || status < 100 || status > 599) return null;
  // Skip our own internal API hits — they pollute the user's view.
  if (p.startsWith("/_next/") || p.startsWith("/__nextjs_")) return null;
  return {
    method,
    path: p,
    status,
    ms: m[4] ? parseInt(m[4], 10) : undefined,
  };
}

export async function GET(
  _req: Request,
  ctx: { params: Promise<{ id: string }> },
) {
  const { id } = await ctx.params;
  const logPath = path.join(workspaceRoot(id), ".jarvis", "dev.log");
  let raw: string;
  try {
    raw = await fs.readFile(logPath, "utf8");
  } catch {
    return NextResponse.json({
      configured: false,
      total: 0,
      errorCount: 0,
      topRoutes: [],
      statusBuckets: { "2xx": 0, "3xx": 0, "4xx": 0, "5xx": 0 },
      recentErrors: [],
      hint:
        "No .jarvis/dev.log yet. Start the dev server (Settings → Sandbox → Start, then have the AI run a `type=start` action) and analytics will populate as your app receives requests.",
    });
  }

  // Walk the last ~5000 lines so very long-running dev sessions don't
  // OOM on parse. Newer entries are at the END of the file.
  const all = raw.split("\n");
  const tail = all.slice(-5000);
  const parsed: LogLine[] = [];
  for (const l of tail) {
    const p = parseLine(l);
    if (p) parsed.push(p);
  }

  if (parsed.length === 0) {
    return NextResponse.json({
      configured: true,
      total: 0,
      errorCount: 0,
      topRoutes: [],
      statusBuckets: { "2xx": 0, "3xx": 0, "4xx": 0, "5xx": 0 },
      recentErrors: [],
      hint:
        "No HTTP request lines found in .jarvis/dev.log yet. Visit your app at the Preview URL to start collecting.",
    });
  }

  // Aggregate per route.
  const routeCounts = new Map<
    string,
    { count: number; errors: number; totalMs: number; msSamples: number }
  >();
  const buckets = { "2xx": 0, "3xx": 0, "4xx": 0, "5xx": 0 };
  let errorCount = 0;
  for (const p of parsed) {
    const key = `${p.method} ${normalizePath(p.path)}`;
    const cur = routeCounts.get(key) ?? {
      count: 0,
      errors: 0,
      totalMs: 0,
      msSamples: 0,
    };
    cur.count += 1;
    if (p.status >= 400) cur.errors += 1;
    if (typeof p.ms === "number") {
      cur.totalMs += p.ms;
      cur.msSamples += 1;
    }
    routeCounts.set(key, cur);

    if (p.status >= 200 && p.status < 300) buckets["2xx"] += 1;
    else if (p.status >= 300 && p.status < 400) buckets["3xx"] += 1;
    else if (p.status >= 400 && p.status < 500) {
      buckets["4xx"] += 1;
      errorCount += 1;
    } else if (p.status >= 500) {
      buckets["5xx"] += 1;
      errorCount += 1;
    }
  }

  const topRoutes = [...routeCounts.entries()]
    .sort((a, b) => b[1].count - a[1].count)
    .slice(0, 20)
    .map(([key, v]) => {
      const [method, ...rest] = key.split(" ");
      return {
        method,
        path: rest.join(" "),
        count: v.count,
        errorRate: v.count === 0 ? 0 : v.errors / v.count,
        avgMs: v.msSamples > 0 ? Math.round(v.totalMs / v.msSamples) : null,
      };
    });

  // Most recent error lines (highest 4xx/5xx in the tail).
  const recentErrors = parsed
    .filter((p) => p.status >= 400)
    .slice(-15)
    .reverse();

  return NextResponse.json({
    configured: true,
    total: parsed.length,
    errorCount,
    topRoutes,
    statusBuckets: buckets,
    recentErrors,
  });
}

// Collapse trailing IDs in paths so /api/users/123 + /api/users/456 +
// /api/users/789 don't bury the "users API" trend below 3 separate
// 1-hit rows. Heuristic: replace any segment that's all-digits or all
// hex-uuid-shape with `:id`.
function normalizePath(p: string): string {
  return p
    .split("/")
    .map((seg) => {
      if (/^\d+$/.test(seg)) return ":id";
      if (/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(seg))
        return ":id";
      return seg;
    })
    .join("/");
}
