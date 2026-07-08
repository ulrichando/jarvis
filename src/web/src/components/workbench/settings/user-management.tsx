"use client";

import { useQuery, useQueryClient, useMutation } from "@tanstack/react-query";
import { Trash2, Users } from "lucide-react";
import { toast } from "sonner";
import { Section } from "./shared";

// ── User Management (app users in workspace SQLite) ───────────────────

export type AppUser = Record<string, unknown> & { id?: string; email?: string };

export function UserMgmtSection({ workspaceId }: { workspaceId: string }) {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["ws", workspaceId, "app-users"],
    queryFn: async () => {
      const r = await fetch(`/api/workspace/${workspaceId}/app-users`);
      return (await r.json()) as {
        configured: boolean;
        users: AppUser[];
        rowCount: number;
        columns?: string[];
        hint?: string;
        error?: string;
      };
    },
    refetchInterval: 15000,
  });

  const remove = useMutation({
    mutationFn: async (id: string) => {
      const r = await fetch(
        `/api/workspace/${workspaceId}/app-users?id=${encodeURIComponent(id)}`,
        { method: "DELETE" },
      );
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(j.message ?? j.error ?? r.statusText);
      }
      return r.json();
    },
    onSuccess: () => {
      toast.success("User removed");
      qc.invalidateQueries({ queryKey: ["ws", workspaceId, "app-users"] });
    },
    onError: (err: Error) => toast.error(`Remove failed: ${err.message}`),
  });

  return (
    <Section
      title="User Management"
      icon={<Users className="size-3.5" />}
      hint="Operates on the deployed app's `users` table. Read-only listing + delete; password reset / invite / role assignment require app-level auth integration (V2)."
    >
      {isLoading ? (
        <p className="text-[12px] text-muted-foreground">Loading…</p>
      ) : !data?.configured ? (
        <p className="text-[12px] text-muted-foreground">
          {data?.hint ?? "No users table yet."}
        </p>
      ) : data.error ? (
        <p className="text-[11.5px] text-destructive/85">{data.error}</p>
      ) : data.users.length === 0 ? (
        <p className="text-[12px] text-muted-foreground">
          {data.hint ?? "0 users registered."}
        </p>
      ) : (
        <>
          <div className="mb-2 text-[11px] text-muted-foreground">
            {data.rowCount.toLocaleString()} total
            {data.users.length < data.rowCount && ` · showing first ${data.users.length}`}
          </div>
          <div className="space-y-1">
            {data.users.map((u, i) => (
              <div
                key={String(u.id ?? i)}
                className="flex items-center gap-2 rounded-md border border-border/40 px-3 py-2 text-[12px]"
              >
                <span className="flex-1 truncate font-mono">
                  {String(u.email ?? u.id ?? "(no email)")}
                </span>
                {u.role ? (
                  <span className="rounded bg-muted px-1.5 py-0 text-[10px] uppercase tracking-wider text-muted-foreground">
                    {String(u.role)}
                  </span>
                ) : null}
                {u.created_at ? (
                  <span className="text-[11px] text-muted-foreground">
                    {String(u.created_at)}
                  </span>
                ) : null}
                <button
                  type="button"
                  onClick={() => {
                    const idStr = String(u.id ?? "");
                    if (!idStr) return;
                    if (confirm(`Delete user ${u.email ?? idStr}?`))
                      remove.mutate(idStr);
                  }}
                  className="rounded p-1 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                  aria-label="Delete user"
                  disabled={!u.id}
                >
                  <Trash2 className="size-3.5" />
                </button>
              </div>
            ))}
          </div>
        </>
      )}
    </Section>
  );
}
