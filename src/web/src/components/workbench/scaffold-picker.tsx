"use client";

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Blocks, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";

type Scaffold = {
  id: string;
  label: string;
  description: string;
  stack: string[];
};

type Props = {
  workspaceId: string;
  // Fired after a scaffold is successfully applied so the parent can
  // collapse the picker / refresh the file tree.
  onApplied?: () => void;
};

async function fetchScaffolds(workspaceId: string) {
  const r = await fetch(`/api/workspace/${workspaceId}/scaffold`);
  if (!r.ok) throw new Error("scaffold list failed");
  return r.json() as Promise<{ scaffolds: Scaffold[]; hasFiles: boolean }>;
}

async function applyScaffold(workspaceId: string, scaffoldId: string) {
  const r = await fetch(`/api/workspace/${workspaceId}/scaffold`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ scaffoldId }),
  });
  if (!r.ok) {
    const j = await r.json().catch(() => ({}));
    throw new Error(j.error ?? `HTTP ${r.status}`);
  }
  return r.json();
}

export function ScaffoldPicker({ workspaceId, onApplied }: Props) {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["ws", workspaceId, "scaffolds"],
    queryFn: () => fetchScaffolds(workspaceId),
    refetchOnWindowFocus: false,
  });
  const [applyingId, setApplyingId] = useState<string | null>(null);

  // Hide the picker once the workspace has files — the user has either
  // scaffolded or asked the model to start writing.
  if (!isLoading && data?.hasFiles) return null;

  const onPick = async (id: string) => {
    if (applyingId) return;
    setApplyingId(id);
    try {
      const r = await applyScaffold(workspaceId, id);
      toast.success(
        `Scaffolded ${id} (${r.copied?.length ?? 0} files). ${r.installed ? "Installed deps. " : ""}${r.started ? "Dev server starting." : ""}`,
      );
      qc.invalidateQueries({ queryKey: ["ws", workspaceId, "tree"] });
      qc.invalidateQueries({ queryKey: ["ws", workspaceId, "scaffolds"] });
      qc.invalidateQueries({ queryKey: ["ws", workspaceId, "preview"] });
      onApplied?.();
    } catch (e) {
      toast.error(`Scaffold failed: ${(e as Error).message}`);
    } finally {
      setApplyingId(null);
    }
  };

  return (
    <div className="rounded-lg border border-border/50 bg-muted/10 p-4 space-y-3">
      <div className="flex items-center gap-2">
        <Blocks className="size-4 text-primary" />
        <p className="text-[13px] font-medium text-foreground/90">
          Start from a scaffold
        </p>
      </div>
      <p className="text-[11px] text-muted-foreground leading-5">
        One click drops a working starter into this workspace, runs{" "}
        <code className="font-mono">bun install</code>, and boots the dev server.
        Then ask Jarvis to extend it.
      </p>
      <div className="space-y-1.5">
        {(data?.scaffolds ?? []).map((s) => (
          <button
            key={s.id}
            onClick={() => onPick(s.id)}
            disabled={applyingId !== null}
            className={cn(
              "w-full text-left rounded-md border border-border/40 px-3 py-2 transition-colors group",
              applyingId === s.id
                ? "bg-primary/10 border-primary/40 cursor-wait"
                : "hover:bg-accent/30 hover:border-border/70",
              applyingId !== null && applyingId !== s.id && "opacity-40 cursor-not-allowed",
            )}
          >
            <div className="flex items-start justify-between gap-2">
              <div className="flex-1 min-w-0">
                <div className="text-[13px] font-medium">{s.label}</div>
                <div className="text-[11px] text-muted-foreground mt-0.5 line-clamp-2">
                  {s.description}
                </div>
                <div className="flex flex-wrap gap-1 mt-1.5">
                  {s.stack.map((tag) => (
                    <span
                      key={tag}
                      className="text-[10px] px-1.5 py-0.5 rounded bg-supporting/40 text-muted-foreground"
                    >
                      {tag}
                    </span>
                  ))}
                </div>
              </div>
              {applyingId === s.id && (
                <Loader2 className="size-3.5 text-primary animate-spin shrink-0 mt-1" />
              )}
            </div>
          </button>
        ))}
      </div>
      <p className="text-[10px] text-muted-foreground/70 leading-4">
        Or skip and just describe what you want — Jarvis will start from scratch.
      </p>
    </div>
  );
}
