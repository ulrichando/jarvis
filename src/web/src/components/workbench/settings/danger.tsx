"use client";

import { useMutation } from "@tanstack/react-query";
import { AlertTriangle, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { apiDeleteWorkspace } from "@/lib/workspace/client";
import { Section } from "./shared";

export async function postClear(id: string) {
  const r = await fetch(`/api/workspace/${id}/clear`, { method: "POST" });
  if (!r.ok) {
    const j = await r.json().catch(() => ({}));
    throw new Error(j.error ?? r.statusText);
  }
  return r.json();
}

// ── Danger zone (clear + delete) ───────────────────────────────────────

export function DangerSection({
  workspaceId,
  workspaceName,
  onDelete,
  onCleared,
}: {
  workspaceId: string;
  workspaceName: string;
  onDelete: () => void;
  onCleared: () => void;
}) {
  const clear = useMutation({
    mutationFn: () => postClear(workspaceId),
    onSuccess: () => {
      toast.success("Workspace files cleared");
      onCleared();
    },
    onError: (err: Error) => toast.error(`Clear failed: ${err.message}`),
  });
  const del = useMutation({
    mutationFn: () => apiDeleteWorkspace(workspaceId),
    onSuccess: () => {
      toast.success("Workspace deleted");
      onDelete();
    },
    onError: (err: Error) => toast.error(`Delete failed: ${err.message}`),
  });

  return (
    <Section
      title="Danger zone"
      icon={<AlertTriangle className="size-3.5 text-destructive" />}
      tone="danger"
    >
      <div className="space-y-3">
        <div className="flex items-start justify-between gap-3 rounded-md border border-amber-500/30 bg-amber-500/5 p-3">
          <div className="flex-1 text-[12px]">
            <div className="font-medium">Reset files</div>
            <p className="mt-0.5 text-muted-foreground">
              Deletes everything except the .jarvis settings folder. Useful when
              you want to regenerate from scratch.
            </p>
          </div>
          <button
            type="button"
            onClick={() => {
              if (
                confirm(
                  `Clear all files in "${workspaceName}"? Settings (brand, etc.) survive.`,
                )
              )
                clear.mutate();
            }}
            disabled={clear.isPending}
            className="shrink-0 rounded-md border border-amber-500/50 px-3 py-1.5 text-[11.5px] text-amber-500 hover:bg-amber-500/10 disabled:opacity-50"
          >
            {clear.isPending ? "Clearing…" : "Reset"}
          </button>
        </div>
        <div className="flex items-start justify-between gap-3 rounded-md border border-destructive/30 bg-destructive/5 p-3">
          <div className="flex-1 text-[12px]">
            <div className="font-medium text-destructive">Delete workspace</div>
            <p className="mt-0.5 text-muted-foreground">
              Removes the workspace, its files, and the sandbox container. Cannot
              be undone.
            </p>
          </div>
          <button
            type="button"
            onClick={() => {
              if (
                confirm(
                  `Delete workspace "${workspaceName}"? Files and the sandbox container will be removed.`,
                )
              )
                del.mutate();
            }}
            disabled={del.isPending}
            className="shrink-0 rounded-md border border-destructive/50 px-3 py-1.5 text-[11.5px] text-destructive hover:bg-destructive/10 disabled:opacity-50"
          >
            <span className="inline-flex items-center gap-1.5">
              <Trash2 className="size-3.5" />
              {del.isPending ? "Deleting…" : "Delete"}
            </span>
          </button>
        </div>
      </div>
    </Section>
  );
}
