"use client";

import { useQuery, useQueryClient, useMutation } from "@tanstack/react-query";
import { Loader2, Play, Square, Trash2, RefreshCw } from "lucide-react";
import { useRouter } from "next/navigation";
import { apiDeleteWorkspace } from "@/lib/workspace/client";
import { cn } from "@/lib/utils";

type Runtime = {
  mode: "docker" | "local";
  reason?: string;
  state: "running" | "stopped" | "absent";
  ports: Record<string, number>;
};

async function fetchRuntime(id: string): Promise<Runtime> {
  return (await fetch(`/api/workspace/${id}/runtime`)).json();
}
async function postAction(id: string, action: "start" | "stop"): Promise<Runtime> {
  return (await fetch(`/api/workspace/${id}/runtime`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action }),
  })).json();
}

type Props = {
  workspaceId: string;
  workspaceName: string;
};

export function SettingsTab({ workspaceId, workspaceName }: Props) {
  const qc = useQueryClient();
  const router = useRouter();

  const { data: rt } = useQuery({
    queryKey: ["ws", workspaceId, "runtime"],
    queryFn: () => fetchRuntime(workspaceId),
    refetchInterval: 5000,
  });

  const start = useMutation({
    mutationFn: () => postAction(workspaceId, "start"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["ws", workspaceId, "runtime"] }),
  });
  const stop = useMutation({
    mutationFn: () => postAction(workspaceId, "stop"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["ws", workspaceId, "runtime"] }),
  });
  const del = useMutation({
    mutationFn: () => apiDeleteWorkspace(workspaceId),
    onSuccess: () => router.replace("/workbench"),
  });

  const isDocker = rt?.mode === "docker";
  const state = rt?.state ?? "absent";
  const ports = Object.entries(rt?.ports ?? {}).sort(
    ([a], [b]) => Number(a) - Number(b),
  );

  return (
    <div className="h-full overflow-y-auto px-6 py-6">
      <div className="mx-auto max-w-2xl space-y-6">
        <div>
          <h2 className="text-lg font-semibold">Workspace settings</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            <span className="font-mono">{workspaceName}</span> ·{" "}
            <span className="font-mono text-xs">{workspaceId}</span>
          </p>
        </div>

        <Section title="Sandbox runtime">
          <KeyVal label="Mode" value={isDocker ? "docker" : "local (host shell)"} />
          <KeyVal
            label="State"
            value={
              <span
                className={cn(
                  "rounded px-1.5 py-0.5 text-[10px] uppercase tracking-wider",
                  state === "running"
                    ? "bg-emerald-500/15 text-emerald-400"
                    : state === "stopped"
                      ? "bg-amber-500/15 text-amber-400"
                      : "bg-muted text-muted-foreground",
                )}
              >
                {state}
              </span>
            }
          />
          {!isDocker && rt?.reason === "image_missing" && (
            <p className="text-xs text-muted-foreground">
              Sandbox image not built. Run{" "}
              <code className="font-mono rounded bg-muted px-1.5 py-0.5">
                npm run build:image
              </code>{" "}
              from the web project.
            </p>
          )}
          {isDocker && (
            <div className="flex gap-2 pt-1">
              {state !== "running" && (
                <button
                  onClick={() => start.mutate()}
                  disabled={start.isPending}
                  className="flex items-center gap-1.5 rounded-md border border-border/60 px-3 py-1.5 text-[12px] hover:bg-accent disabled:opacity-40"
                >
                  {start.isPending ? (
                    <Loader2 className="size-3.5 animate-spin" />
                  ) : (
                    <Play className="size-3.5" />
                  )}
                  Start sandbox
                </button>
              )}
              {state === "running" && (
                <button
                  onClick={() => stop.mutate()}
                  disabled={stop.isPending}
                  className="flex items-center gap-1.5 rounded-md border border-border/60 px-3 py-1.5 text-[12px] hover:bg-accent disabled:opacity-40"
                >
                  {stop.isPending ? (
                    <Loader2 className="size-3.5 animate-spin" />
                  ) : (
                    <Square className="size-3.5" />
                  )}
                  Stop sandbox
                </button>
              )}
              <button
                onClick={() => qc.invalidateQueries({ queryKey: ["ws", workspaceId, "runtime"] })}
                className="flex items-center gap-1.5 rounded-md border border-border/60 px-3 py-1.5 text-[12px] hover:bg-accent"
              >
                <RefreshCw className="size-3.5" />
                Refresh
              </button>
            </div>
          )}
        </Section>

        <Section title="Exposed ports">
          {ports.length === 0 ? (
            <p className="text-xs text-muted-foreground">
              {state === "running" ? "(none)" : "Sandbox not running."}
            </p>
          ) : (
            <div className="space-y-1">
              {ports.map(([cp, hp]) => (
                <div
                  key={cp}
                  className="flex items-center justify-between font-mono text-[12px] rounded-md border border-border/40 px-3 py-1.5"
                >
                  <span>container :{cp}</span>
                  <span className="text-muted-foreground">→ host :{hp}</span>
                </div>
              ))}
            </div>
          )}
        </Section>

        <Section title="Danger zone">
          <button
            onClick={() => {
              if (
                confirm(
                  `Delete workspace "${workspaceName}"? Files and the sandbox container will be removed.`,
                )
              ) {
                del.mutate();
              }
            }}
            className="flex items-center gap-1.5 rounded-md border border-destructive/50 px-3 py-1.5 text-[12px] text-destructive hover:bg-destructive/10"
          >
            <Trash2 className="size-3.5" />
            Delete workspace
          </button>
        </Section>
      </div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="space-y-2">
      <h3 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
        {title}
      </h3>
      <div className="space-y-2 rounded-lg border border-border/50 bg-card/30 p-4">
        {children}
      </div>
    </div>
  );
}

function KeyVal({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between text-[12px]">
      <span className="text-muted-foreground">{label}</span>
      <span>{value}</span>
    </div>
  );
}
