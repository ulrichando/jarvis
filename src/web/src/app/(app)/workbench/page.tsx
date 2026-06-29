"use client";

import { useState } from "react";
import Link from "next/link";
import { useQuery, useQueryClient, useMutation } from "@tanstack/react-query";
import { Plus, Trash2, FolderCode } from "lucide-react";
import {
  apiListWorkspaces,
  apiCreateWorkspace,
  apiDeleteWorkspace,
} from "@/lib/workspace/client";
import { Button } from "@/components/ui/button";
import { SidebarToggle } from "@/components/layout/sidebar-toggle";

export default function WorkbenchListPage() {
  const qc = useQueryClient();
  const [name, setName] = useState("");

  // Workbench tab lists only kind="workbench" workspaces. Design
  // workspaces stay separate.
  const { data: workspaces = [], isLoading } = useQuery({
    queryKey: ["workspaces", "workbench"],
    queryFn: () => apiListWorkspaces("workbench"),
  });

  const create = useMutation({
    mutationFn: (n: string) => apiCreateWorkspace(n, "workbench"),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["workspaces", "workbench"] });
      setName("");
    },
  });

  const del = useMutation({
    mutationFn: (id: string) => apiDeleteWorkspace(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["workspaces", "workbench"] });
    },
  });

  return (
    <div className="flex h-full flex-col">
      {/* Tiny header just for the SidebarToggle — the workbench paths
          (this list AND /workbench/[id]) are excluded from both TopBar
          and the Sidebar's floating button, so without this row there's
          NO way to reopen the global sidebar from here. */}
      <header className="flex h-11 shrink-0 items-center border-b border-border/60">
        <SidebarToggle />
      </header>
      <div className="flex-1 overflow-y-auto px-6 py-8">
        <div className="mx-auto max-w-3xl">
        <h1 className="font-serif text-2xl font-semibold tracking-tight mb-1">
          Workbench
        </h1>
        <p className="text-sm text-muted-foreground mb-6">
          Build and edit projects with files, an editor, and a terminal —
          rooted at <code className="font-mono text-xs">~/.jarvis/workspaces/</code>.
        </p>

        {/* Boxed + labeled so it reads as "name a NEW workspace", not a
            search field hovering over the list below. */}
        <div className="mb-6 rounded-lg border border-border/60 bg-muted/20 p-4">
          <label
            htmlFor="new-workspace-name"
            className="mb-2 block text-xs font-medium uppercase tracking-wider text-muted-foreground"
          >
            Create a workspace
          </label>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (!name.trim()) return;
              create.mutate(name.trim());
            }}
            className="flex gap-2"
          >
            <input
              id="new-workspace-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Name it — e.g. landing-page"
              autoFocus
              className="flex-1 rounded-md border border-border/60 bg-background px-3 py-2 text-sm outline-none transition-colors focus:border-foreground/30"
            />
            <Button type="submit" disabled={!name.trim() || create.isPending}>
              <Plus className="size-4" />
              {create.isPending ? "Creating…" : "Create"}
            </Button>
          </form>
          <p className="mt-2 text-xs text-muted-foreground">
            {create.isError
              ? "Couldn't create that workspace — try a different name."
              : "Type a name, then Create — a project folder by that name appears below."}
          </p>
        </div>

        <div className="space-y-1">
          {isLoading ? (
            <div className="text-sm text-muted-foreground">loading…</div>
          ) : workspaces.length === 0 ? (
            <div className="text-sm text-muted-foreground italic">
              No workspaces yet — create one above.
            </div>
          ) : (
            workspaces.map((w) => (
              <div
                key={w.id}
                className="group flex items-center gap-3 rounded-md border border-border/40 px-3 py-2 hover:bg-accent/40"
              >
                <FolderCode className="size-4 text-muted-foreground" />
                <Link
                  href={`/workbench/${w.id}`}
                  className="flex-1 min-w-0 text-sm font-medium hover:underline"
                >
                  {w.name}
                </Link>
                <span className="text-[11px] text-muted-foreground">
                  {new Date(w.updatedAt).toLocaleDateString()}
                </span>
                <button
                  onClick={() => {
                    if (confirm(`Delete workspace "${w.name}"? This removes all files.`)) {
                      del.mutate(w.id);
                    }
                  }}
                  className="opacity-0 group-hover:opacity-60 hover:opacity-100 transition-opacity"
                  aria-label="delete"
                >
                  <Trash2 className="size-3.5 text-muted-foreground" />
                </button>
              </div>
            ))
          )}
        </div>
        </div>
      </div>
    </div>
  );
}
