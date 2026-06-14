"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { ChevronDown, Loader2, Pencil, Plus, Trash2 } from "lucide-react";
import {
  apiCreateWorkspace,
  apiDeleteWorkspace,
  apiRenameWorkspace,
  type Workspace,
} from "@/lib/workspace/client";
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
      const ws = await apiCreateWorkspace(name.trim(), "design");
      onChanged?.();
      router.push(`/design?ws=${encodeURIComponent(ws.id)}`);
    } catch (err) {
      toast.error(`Create failed: ${err instanceof Error ? err.message : err}`);
    } finally {
      setBusy(null);
    }
  };

  const rename = async (id: string, currentName: string) => {
    const next = window.prompt("Rename project", currentName);
    if (!next || next.trim() === currentName) return;
    setBusy(id);
    try {
      await apiRenameWorkspace(id, next.trim());
      onChanged?.();
      router.refresh();
    } catch (err) {
      toast.error(`Rename failed: ${err instanceof Error ? err.message : err}`);
    } finally {
      setBusy(null);
    }
  };

  const remove = async (id: string, name: string) => {
    const isCurrent = id === current.id;
    const others = projects.filter((p) => p.id !== id);
    const willAutoCreate = isCurrent && others.length === 0;

    const promptMsg = isCurrent
      ? willAutoCreate
        ? `Delete "${name}"? It's your only project — a fresh "Untitled design" will be created and opened.`
        : `Delete "${name}"? You'll switch to "${others[0].name}" automatically.`
      : `Delete project "${name}"? Files inside it will be removed.`;

    if (!window.confirm(promptMsg)) return;

    setBusy(id);
    try {
      // For current-project deletion we must move the user to a target before
      // wiping the workspace they're sitting in — otherwise the design view
      // unmounts mid-delete and we'd refetch a 404.
      if (isCurrent) {
        const target = others[0] ?? (await apiCreateWorkspace("Untitled design", "design"));
        await apiDeleteWorkspace(id);
        onChanged?.();
        router.push(`/design?ws=${encodeURIComponent(target.id)}`);
        return;
      }
      await apiDeleteWorkspace(id);
      onChanged?.();
      router.refresh();
    } catch (err) {
      toast.error(`Delete failed: ${err instanceof Error ? err.message : err}`);
    } finally {
      setBusy(null);
    }
  };

  return (
    <details className="relative flex h-full min-w-0">
      <summary
        className={cn(
          "flex h-full min-w-0 cursor-pointer list-none items-center gap-2.5 px-3",
          "hover:bg-muted/30 transition-colors",
          "[&::-webkit-details-marker]:hidden",
        )}
      >
        {/* No logo here — the global Sidebar already brands the app.
            Showing a second mark stacks two logos in the same eye-line. */}
        <span className="truncate text-[13px] font-medium tracking-tight text-foreground/90">
          {current.name}
        </span>
        <ChevronDown className="size-3 shrink-0 text-muted-foreground/70" />
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
                  aria-label={`Rename ${p.name}`}
                  onClick={() => rename(p.id, p.name)}
                  disabled={removing}
                  title="Rename project"
                  className={cn(
                    "rounded p-1 text-muted-foreground/60 transition-colors",
                    "opacity-0 group-hover:opacity-100",
                    "hover:bg-muted hover:text-foreground",
                    removing && "opacity-30 pointer-events-none",
                  )}
                >
                  <Pencil className="size-3" />
                </button>
                <button
                  type="button"
                  aria-label={`Delete ${p.name}`}
                  onClick={() => remove(p.id, p.name)}
                  disabled={removing}
                  title="Delete project"
                  className={cn(
                    "rounded p-1 text-muted-foreground/60 transition-colors",
                    "opacity-0 group-hover:opacity-100",
                    "hover:bg-destructive/10 hover:text-destructive",
                    removing && "opacity-30 pointer-events-none",
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
