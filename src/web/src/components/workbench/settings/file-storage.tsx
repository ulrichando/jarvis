"use client";

import { useQuery } from "@tanstack/react-query";
import { HardDrive, RefreshCw } from "lucide-react";
import { cn } from "@/lib/utils";
import { type TreeEntry, Section } from "./shared";

// ── File Storage (top-level files + sizes) ────────────────────────────

export type FileStat = { name: string; bytes: number; type: "file" | "dir" };

export async function fetchTopLevel(workspaceId: string): Promise<FileStat[]> {
  const r = await fetch(`/api/workspace/${workspaceId}/tree?path=`);
  if (!r.ok) return [];
  const j: { entries?: TreeEntry[] } = await r.json();
  // Tree endpoint doesn't expose sizes directly; show the top-level
  // entries with type. A future iteration can add a `?withSizes=1`
  // option to the tree endpoint to populate `bytes`.
  return (j.entries ?? []).map((e) => ({
    name: e.name,
    bytes: 0,
    type: e.type,
  }));
}

export function FileStorageSection({
  workspaceId,
}: {
  workspaceId: string;
}) {
  const { data: entries, isLoading, refetch } = useQuery({
    queryKey: ["ws", workspaceId, "top-level"],
    queryFn: () => fetchTopLevel(workspaceId),
    refetchOnWindowFocus: false,
  });

  const dirs = (entries ?? []).filter((e) => e.type === "dir");
  const files = (entries ?? []).filter((e) => e.type === "file");

  return (
    <Section
      title="File storage"
      icon={<HardDrive className="size-3.5" />}
      hint="Workspace filesystem at /workspace inside the sandbox. The Code tab is the full file browser; this surface shows the top level so you can see project shape at a glance."
    >
      <div className="mb-2 flex items-center justify-between text-[11px] text-muted-foreground">
        <span>
          {dirs.length} folder{dirs.length === 1 ? "" : "s"} ·{" "}
          {files.length} file{files.length === 1 ? "" : "s"}
        </span>
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
      ) : (entries?.length ?? 0) === 0 ? (
        <p className="text-[12px] text-muted-foreground">
          Workspace is empty.
        </p>
      ) : (
        <div className="grid grid-cols-2 gap-1.5">
          {[...dirs, ...files].map((e) => (
            <div
              key={e.name}
              className="flex items-center gap-2 rounded-md border border-border/40 px-2.5 py-1.5 font-mono text-[11.5px]"
            >
              <span
                className={cn(
                  "shrink-0 rounded px-1 py-0 text-[9px] uppercase tracking-wider",
                  e.type === "dir"
                    ? "bg-primary/15 text-primary"
                    : "bg-muted text-muted-foreground",
                )}
              >
                {e.type === "dir" ? "dir" : "file"}
              </span>
              <span className="truncate">{e.name}</span>
            </div>
          ))}
        </div>
      )}
    </Section>
  );
}
