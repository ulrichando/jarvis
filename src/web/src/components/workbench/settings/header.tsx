"use client";

import { useState } from "react";
import { useQueryClient, useMutation } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  type WorkspaceMeta,
  patchWorkspace,
  KvRow,
  CopyButton,
  formatDate,
} from "./shared";

// ── Header (name editor + IDs + dates) ──────────────────────────────────

export function Header({
  ws,
  fallbackName,
  workspaceId,
}: {
  ws: WorkspaceMeta | null;
  fallbackName: string;
  workspaceId: string;
}) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [draftName, setDraftName] = useState("");
  const name = ws?.name ?? fallbackName;

  const rename = useMutation({
    mutationFn: (next: string) => patchWorkspace(workspaceId, { name: next }),
    onSuccess: () => {
      toast.success("Workspace renamed");
      qc.invalidateQueries({ queryKey: ["ws", workspaceId] });
      qc.invalidateQueries({ queryKey: ["workspace", workspaceId] });
      qc.invalidateQueries({ queryKey: ["workspaces"] });
      setEditing(false);
    },
    onError: (err: Error) => toast.error(`Rename failed: ${err.message}`),
  });

  return (
    <div className="space-y-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          {editing ? (
            <div className="flex items-center gap-2">
              <input
                autoFocus
                value={draftName}
                onChange={(e) => setDraftName(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") rename.mutate(draftName);
                  if (e.key === "Escape") setEditing(false);
                }}
                placeholder={name}
                maxLength={80}
                className="rounded-md border border-border bg-background px-2 py-1 text-base font-semibold outline-none focus:border-primary"
              />
              <button
                type="button"
                onClick={() => rename.mutate(draftName)}
                disabled={rename.isPending || !draftName.trim()}
                className="rounded-md border border-border px-2 py-1 text-[12px] hover:bg-accent disabled:opacity-50"
              >
                Save
              </button>
              <button
                type="button"
                onClick={() => setEditing(false)}
                className="rounded-md border border-border px-2 py-1 text-[12px] hover:bg-accent"
              >
                Cancel
              </button>
            </div>
          ) : (
            <h2
              className="cursor-text text-lg font-semibold hover:opacity-80"
              onClick={() => {
                setDraftName(name);
                setEditing(true);
              }}
              title="Click to rename"
            >
              {name}
            </h2>
          )}
        </div>
      </div>
      <div className="grid grid-cols-2 gap-x-6 gap-y-1.5 text-[12px]">
        <KvRow label="Workspace ID">
          <CopyButton value={workspaceId} className="font-mono" />
        </KvRow>
        <KvRow label="Kind">
          <span className="font-mono">{ws?.kind ?? "design"}</span>
        </KvRow>
        <KvRow label="Created">
          <span>{ws ? formatDate(ws.createdAt) : "—"}</span>
        </KvRow>
        <KvRow label="Updated">
          <span>{ws ? formatDate(ws.updatedAt) : "—"}</span>
        </KvRow>
        {ws?.conversationId && (
          <KvRow label="Conversation">
            <CopyButton value={ws.conversationId} className="font-mono" />
          </KvRow>
        )}
      </div>
    </div>
  );
}
