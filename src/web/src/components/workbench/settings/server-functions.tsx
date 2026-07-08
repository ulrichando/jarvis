"use client";

import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { RefreshCw, Server } from "lucide-react";
import { type TreeEntry, Section } from "./shared";

// ── Server Functions (lists app/api/* routes) ─────────────────────────

export async function listAllRoutes(workspaceId: string): Promise<string[]> {
  // Walk app/api recursively. Server Functions = files named route.ts /
  // route.tsx / route.js inside app/api/.
  async function walk(rel: string): Promise<string[]> {
    const url = `/api/workspace/${workspaceId}/tree?path=${encodeURIComponent(rel)}`;
    const r = await fetch(url);
    if (!r.ok) return [];
    const j: { entries?: TreeEntry[] } = await r.json();
    const out: string[] = [];
    for (const e of j.entries ?? []) {
      if (e.type === "dir") {
        out.push(...(await walk(e.path)));
      } else if (/^route\.(ts|tsx|js|mjs)$/.test(e.name)) {
        out.push(e.path);
      }
    }
    return out;
  }
  return walk("app/api");
}

export function ServerFunctionsSection({
  workspaceId,
}: {
  workspaceId: string;
}) {
  const { data, isLoading, refetch } = useQuery({
    queryKey: ["ws", workspaceId, "routes"],
    queryFn: () => listAllRoutes(workspaceId),
    refetchOnWindowFocus: false,
  });

  // Convert "app/api/users/[id]/route.ts" → "/api/users/[id]" for display.
  const fnList = useMemo(() => {
    const items = (data ?? []).map((p) => {
      const route = "/" + p.replace(/^app\//, "").replace(/\/route\.[a-z]+$/i, "");
      return { file: p, route };
    });
    items.sort((a, b) => a.route.localeCompare(b.route));
    return items;
  }, [data]);

  return (
    <Section
      title="Server functions"
      icon={<Server className="size-3.5" />}
      hint="Every app/api/**/route.{ts,tsx,js,mjs} file is a server function. Click a row to open it in the Code tab."
    >
      <div className="mb-2 flex items-center justify-between text-[11px] text-muted-foreground">
        <span>{fnList.length} function{fnList.length === 1 ? "" : "s"}</span>
        <button
          type="button"
          onClick={() => refetch()}
          className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 hover:bg-accent"
        >
          <RefreshCw className="size-3" />
          Refresh
        </button>
      </div>
      {isLoading ? (
        <p className="text-[12px] text-muted-foreground">Loading…</p>
      ) : fnList.length === 0 ? (
        <p className="text-[12px] text-muted-foreground">
          No server functions yet. Once the AI creates files under{" "}
          <code className="font-mono">app/api/</code>, they show up here.
        </p>
      ) : (
        <div className="space-y-1">
          {fnList.map((f) => (
            <div
              key={f.file}
              className="flex items-center justify-between rounded-md border border-border/40 px-3 py-1.5 font-mono text-[11.5px]"
            >
              <span className="truncate">
                <span className="text-foreground">{f.route}</span>
              </span>
              <span className="text-[10.5px] text-muted-foreground">
                {f.file}
              </span>
            </div>
          ))}
        </div>
      )}
    </Section>
  );
}
