"use client";

import { useMutation } from "@tanstack/react-query";
import { Play, Square, RefreshCw, RotateCcw } from "lucide-react";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { type Runtime, Section, KvRow, ActionButton } from "./shared";

export async function postRuntime(
  id: string,
  action: "start" | "stop" | "restart",
): Promise<Runtime> {
  return (
    await fetch(`/api/workspace/${id}/runtime`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action }),
    })
  ).json();
}

// ── Sandbox runtime ─────────────────────────────────────────────────────

export function RuntimeSection({
  rt,
  workspaceId,
  onChanged,
}: {
  rt: Runtime | null;
  workspaceId: string;
  onChanged: () => void;
}) {
  const isDocker = rt?.mode === "docker";
  const state = rt?.state ?? "absent";
  const ports = Object.entries(rt?.ports ?? {}).sort(
    ([a], [b]) => Number(a) - Number(b),
  );

  const start = useMutation({
    mutationFn: () => postRuntime(workspaceId, "start"),
    onSuccess: () => {
      toast.success("Sandbox started");
      onChanged();
    },
    onError: (err: Error) => toast.error(`Start failed: ${err.message}`),
  });
  const stop = useMutation({
    mutationFn: () => postRuntime(workspaceId, "stop"),
    onSuccess: () => {
      toast.success("Sandbox stopped");
      onChanged();
    },
    onError: (err: Error) => toast.error(`Stop failed: ${err.message}`),
  });
  const restart = useMutation({
    mutationFn: () => postRuntime(workspaceId, "restart"),
    onSuccess: () => {
      toast.success("Sandbox restarted with current env vars");
      onChanged();
    },
    onError: (err: Error) => toast.error(`Restart failed: ${err.message}`),
  });

  return (
    <Section title="Sandbox runtime">
      <div className="grid grid-cols-2 gap-x-6 gap-y-1.5 text-[12px]">
        <KvRow label="Mode">
          <span className="font-mono">
            {isDocker ? "docker" : "local (host shell)"}
          </span>
        </KvRow>
        <KvRow label="State">
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
        </KvRow>
      </div>
      {!isDocker && rt?.reason === "image_missing" && (
        <p className="mt-2 text-xs text-muted-foreground">
          Sandbox image not built. Run{" "}
          <code className="rounded bg-muted px-1.5 py-0.5 font-mono">
            npm run build:image
          </code>{" "}
          from the web project.
        </p>
      )}
      {isDocker && (
        <div className="mt-3 flex flex-wrap gap-2">
          {state !== "running" && (
            <ActionButton
              icon={<Play className="size-3.5" />}
              label="Start"
              onClick={() => start.mutate()}
              pending={start.isPending}
            />
          )}
          {state === "running" && (
            <ActionButton
              icon={<Square className="size-3.5" />}
              label="Stop"
              onClick={() => stop.mutate()}
              pending={stop.isPending}
            />
          )}
          <ActionButton
            icon={<RotateCcw className="size-3.5" />}
            label="Restart"
            onClick={() => restart.mutate()}
            pending={restart.isPending}
            tooltip="Recreate container — required after env var changes"
          />
          <ActionButton
            icon={<RefreshCw className="size-3.5" />}
            label="Refresh"
            onClick={() => onChanged()}
          />
        </div>
      )}
      {ports.length > 0 && (
        <div className="mt-3">
          <div className="mb-1 text-[10px] uppercase tracking-wider text-muted-foreground">
            Exposed ports
          </div>
          <div className="space-y-1">
            {ports.map(([cp, hp]) => (
              <div
                key={cp}
                className="flex items-center justify-between rounded-md border border-border/40 px-3 py-1.5 font-mono text-[12px]"
              >
                <span>container :{cp}</span>
                <span className="text-muted-foreground">→ host :{hp}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </Section>
  );
}
