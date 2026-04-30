"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { ChevronDown, Loader2, Palette, Plus, Trash2 } from "lucide-react";
import { apiCreateWorkspace, apiDeleteWorkspace, type Workspace } from "@/lib/workspace/client";
import { cn } from "@/lib/utils";

export function ProjectPicker({
  current,
  projects,
  onChanged,
}: {
  current: { id: string; name: string };
  projects: Workspace[];
  /** Called when the picker has mutated workspace state (created/deleted) so
   *  the parent can refresh — usually via router.refresh(). */
  onChanged?: () => void;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState<"create" | string | null>(null);

  const switchTo = (id: string) => {
    if (id === current.id) return;
    router.push(`/design?ws=${encodeURIComponent(id)}`);
  };

  const create = async () => {
    const name = window.prompt(
      "New project name",
      `Design ${new Date().toLocaleDateString()}`,
    );
    if (!name || !name.trim()) return;
    setBusy("create");
    try {
      const ws = await apiCreateWorkspace(name.trim());
      onChanged?.();
      router.push(`/design?ws=${encodeURIComponent(ws.id)}`);
    } catch (err) {
      window.alert(`Create failed: ${err instanceof Error ? err.message : err}`);
    } finally {
      setBusy(null);
    }
  };

  const remove = async (id: string, name: string) => {
    if (id === current.id) {
      window.alert(
        "Can't delete the project you're currently in. Switch to another first.",
      );
      return;
    }
    if (!window.confirm(`Delete project "${name}"? Files inside it will be removed.`)) {
      return;
    }
    setBusy(id);
    try {
      await apiDeleteWorkspace(id);
      onChanged?.();
      router.refresh();
    } catch (err) {
      window.alert(`Delete failed: ${err instanceof Error ? err.message : err}`);
    } finally {
      setBusy(null);
    }
  };

  return (
    <details className="relative flex h-full">
      <summary
        className={cn(
          "flex h-full min-w-0 cursor-pointer list-none items-center gap-2 px-3",
          "hover:bg-muted/30 transition-colors",
        )}
      >
        <span className="flex size-7 shrink-0 items-center justify-center rounded-md bg-orange-500/15 text-orange-400">
          <Palette className="size-3.5" />
        </span>
        <span className="truncate text-[14px] font-semibold tracking-tight">
          {current.name}
        </span>
        <ChevronDown className="size-3 shrink-0 text-muted-foreground" />
      </summary>

      <div className="absolute left-2 top-full z-30 mt-1 w-72 max-h-[60vh] overflow-y-auto rounded-md border border-border/60 bg-popover shadow-lg">
        <div className="px-3 py-2 text-[10px] font-mono uppercase tracking-[0.18em] text-muted-foreground/70">
          Projects
        </div>
        <div className="space-y-px px-1 pb-1">
          {projects.map((p) => {
            const active = p.id === current.id;
            const removing = busy === p.id;
            return (
              <div
                key={p.id}
                className={cn(
                  "group flex items-center gap-2 rounded-md px-2 py-1.5",
                  active ? "bg-primary/10" : "hover:bg-muted/40",
                )}
              >
                <button
                  type="button"
                  onClick={() => switchTo(p.id)}
                  className="flex flex-1 min-w-0 items-center gap-2 text-left"
                >
                  <span
                    className={cn(
                      "size-1.5 rounded-full shrink-0",
                      active ? "bg-orange-400" : "bg-muted-foreground/40",
                    )}
                  />
                  <span className="truncate text-[13px]">{p.name}</span>
                </button>
                <button
                  type="button"
                  aria-label={`Delete ${p.name}`}
                  onClick={() => remove(p.id, p.name)}
                  disabled={removing || active}
                  title={active ? "Switch projects first" : "Delete project"}
                  className={cn(
                    "rounded p-1 text-muted-foreground/60 transition-colors",
                    "opacity-0 group-hover:opacity-100",
                    "hover:bg-destructive/10 hover:text-destructive",
                    (removing || active) && "opacity-30 pointer-events-none",
                  )}
                >
                  {removing ? (
                    <Loader2 className="size-3 animate-spin" />
                  ) : (
                    <Trash2 className="size-3" />
                  )}
                </button>
              </div>
            );
          })}
        </div>

        <div className="border-t border-border/60 p-1">
          <button
            type="button"
            onClick={create}
            disabled={busy === "create"}
            className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-[13px] font-medium hover:bg-muted/40 disabled:opacity-50"
          >
            {busy === "create" ? (
              <Loader2 className="size-3.5 animate-spin" />
            ) : (
              <Plus className="size-3.5" />
            )}
            New project
          </button>
        </div>
      </div>
    </details>
  );
}
