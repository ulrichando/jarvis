"use client";

import { useState } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { Box, Check, ChevronDown, FolderCode } from "lucide-react";
import { useChatStore } from "@/stores/chat";
import { apiListWorkspaces } from "@/lib/workspace/client";
import { cn } from "@/lib/utils";

export function ComposerWorkspacePicker() {
  const [open, setOpen] = useState(false);
  const targetId = useChatStore((s) => s.targetWorkspaceId);
  const targetName = useChatStore((s) => s.targetWorkspaceName);
  const setTarget = useChatStore((s) => s.setTargetWorkspace);

  const { data: workspaces = [] } = useQuery({
    queryKey: ["workspaces"],
    queryFn: () => apiListWorkspaces(),
  });

  const label = targetId
    ? targetName ?? "workspace"
    : "no workspace";

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className={cn(
          "flex items-center gap-1.5 rounded-lg border border-border/40 px-2 py-1 text-[11px] hover:bg-accent/40 transition-colors",
          targetId && "border-primary/40 bg-primary/5",
        )}
        title="Pick a workspace for AI to write code into"
      >
        {targetId ? (
          <Box className="size-3 text-primary" />
        ) : (
          <FolderCode className="size-3 text-muted-foreground" />
        )}
        <span className="max-w-[120px] truncate">{label}</span>
        <ChevronDown className="size-3 text-muted-foreground" />
      </button>

      {open && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div className="absolute bottom-full left-0 mb-1 z-50 w-64 rounded-lg border border-border/60 bg-popover shadow-lg overflow-hidden">
            <div className="px-3 py-2 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground border-b border-border/40">
              Build target
            </div>
            <button
              type="button"
              onClick={() => {
                setTarget(null, null);
                setOpen(false);
              }}
              className={cn(
                "flex w-full items-center justify-between px-3 py-2 text-[12px] hover:bg-accent/60 transition-colors",
                !targetId && "bg-accent/40",
              )}
            >
              <span className="text-muted-foreground italic">no workspace (chat only)</span>
              {!targetId && <Check className="size-3.5" />}
            </button>
            <div className="max-h-64 overflow-y-auto">
              {workspaces.length === 0 ? (
                <div className="px-3 py-2 text-[11px] text-muted-foreground italic">
                  No workspaces yet.
                </div>
              ) : (
                workspaces.map((w) => (
                  <button
                    key={w.id}
                    type="button"
                    onClick={() => {
                      setTarget(w.id, w.name);
                      setOpen(false);
                    }}
                    className={cn(
                      "flex w-full items-center justify-between gap-2 px-3 py-2 text-[12px] hover:bg-accent/60 transition-colors",
                      targetId === w.id && "bg-accent/40",
                    )}
                  >
                    <div className="flex items-center gap-2 min-w-0">
                      <FolderCode className="size-3.5 shrink-0 text-muted-foreground" />
                      <span className="truncate">{w.name}</span>
                    </div>
                    {targetId === w.id && <Check className="size-3.5 shrink-0" />}
                  </button>
                ))
              )}
            </div>
            <div className="border-t border-border/40 px-3 py-2 text-[11px]">
              <Link
                href="/workbench"
                className="text-muted-foreground hover:text-foreground"
                onClick={() => setOpen(false)}
              >
                Manage workspaces →
              </Link>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
