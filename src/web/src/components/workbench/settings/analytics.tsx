"use client";

import { useQuery } from "@tanstack/react-query";
import { BarChart3 } from "lucide-react";
import { cn } from "@/lib/utils";
import { Section } from "./shared";

// ── Analytics (parsed from dev.log) ───────────────────────────────────

export type AnalyticsResponse = {
  configured: boolean;
  total: number;
  errorCount: number;
  topRoutes: Array<{
    method: string;
    path: string;
    count: number;
    errorRate: number;
    avgMs: number | null;
  }>;
  statusBuckets: { "2xx": number; "3xx": number; "4xx": number; "5xx": number };
  recentErrors: Array<{ method: string; path: string; status: number }>;
  hint?: string;
};

export function AnalyticsSection({ workspaceId }: { workspaceId: string }) {
  const { data, isLoading } = useQuery({
    queryKey: ["ws", workspaceId, "analytics"],
    queryFn: async () => {
      const r = await fetch(`/api/workspace/${workspaceId}/analytics`);
      return (await r.json()) as AnalyticsResponse;
    },
    refetchInterval: 8000,
  });

  return (
    <>
      <Section
        title="Request analytics"
        icon={<BarChart3 className="size-3.5" />}
        hint="Parsed from .jarvis/dev.log — captures requests handled by your dev server. Production analytics (page views from real users) requires deploying with edge instrumentation; that's V2."
      >
        {isLoading ? (
          <p className="text-[12px] text-muted-foreground">Loading…</p>
        ) : !data ? (
          <p className="text-[12px] text-muted-foreground">No data.</p>
        ) : !data.configured ? (
          <p className="text-[12px] text-muted-foreground">{data.hint}</p>
        ) : (
          <>
            <div className="grid grid-cols-4 gap-3">
              <Stat label="Requests" value={data.total.toLocaleString()} />
              <Stat
                label="Errors"
                value={data.errorCount.toLocaleString()}
                tone={data.errorCount > 0 ? "warn" : undefined}
              />
              <Stat
                label="Error rate"
                value={
                  data.total === 0
                    ? "—"
                    : `${((data.errorCount / data.total) * 100).toFixed(1)}%`
                }
                tone={
                  data.total > 0 && data.errorCount / data.total > 0.05
                    ? "warn"
                    : undefined
                }
              />
              <Stat
                label="Routes"
                value={data.topRoutes.length.toString()}
              />
            </div>
            <div className="mt-3 grid grid-cols-4 gap-1.5 text-[11px]">
              {(["2xx", "3xx", "4xx", "5xx"] as const).map((b) => (
                <div
                  key={b}
                  className="rounded border border-border/40 px-2 py-1"
                >
                  <div className="text-muted-foreground">{b}</div>
                  <div className="font-mono">{data.statusBuckets[b]}</div>
                </div>
              ))}
            </div>
          </>
        )}
      </Section>

      {data?.configured && data.topRoutes.length > 0 && (
        <Section title="Top routes">
          <div className="space-y-1">
            {data.topRoutes.map((r) => (
              <div
                key={`${r.method} ${r.path}`}
                className="flex items-center gap-3 rounded-md border border-border/40 px-3 py-1.5 text-[12px]"
              >
                <span className="w-12 shrink-0 rounded bg-muted px-1.5 py-0 text-center text-[10px] uppercase tracking-wider text-muted-foreground">
                  {r.method}
                </span>
                <span className="flex-1 truncate font-mono">{r.path}</span>
                {r.errorRate > 0 && (
                  <span
                    className={cn(
                      "shrink-0 text-[10.5px]",
                      r.errorRate > 0.1
                        ? "text-destructive"
                        : "text-amber-500/85",
                    )}
                  >
                    {(r.errorRate * 100).toFixed(0)}% err
                  </span>
                )}
                {r.avgMs != null && (
                  <span className="shrink-0 text-[11px] text-muted-foreground">
                    ~{r.avgMs}ms
                  </span>
                )}
                <span className="shrink-0 font-mono text-[11px] tabular-nums text-foreground">
                  {r.count.toLocaleString()}
                </span>
              </div>
            ))}
          </div>
        </Section>
      )}

      {data?.configured && data.recentErrors.length > 0 && (
        <Section title={`Recent errors (${data.recentErrors.length})`}>
          <div className="space-y-1">
            {data.recentErrors.map((e, i) => (
              <div
                key={i}
                className="flex items-center gap-2 rounded-md border border-destructive/30 bg-destructive/5 px-3 py-1.5 text-[11.5px]"
              >
                <span className="rounded bg-destructive/15 px-1.5 py-0 font-mono text-[10px] text-destructive">
                  {e.status}
                </span>
                <span className="rounded bg-muted px-1.5 py-0 text-[10px] uppercase tracking-wider text-muted-foreground">
                  {e.method}
                </span>
                <span className="flex-1 truncate font-mono">{e.path}</span>
              </div>
            ))}
          </div>
        </Section>
      )}
    </>
  );
}

export function Stat({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "warn";
}) {
  return (
    <div
      className={cn(
        "rounded-md border px-3 py-2",
        tone === "warn"
          ? "border-amber-500/30 bg-amber-500/5"
          : "border-border/40 bg-card/30",
      )}
    >
      <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
        {label}
      </div>
      <div className="mt-0.5 font-mono text-[15px]">{value}</div>
    </div>
  );
}
