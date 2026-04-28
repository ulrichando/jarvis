"use client";

import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient, useMutation } from "@tanstack/react-query";
import {
  Box,
  Square,
  ExternalLink,
  Loader2,
  RefreshCw,
  ChevronDown,
  Eye,
} from "lucide-react";
import { cn } from "@/lib/utils";

type Runtime = {
  mode: "docker" | "local";
  reason?: string;
  state: "running" | "stopped" | "absent";
  ports: Record<string, number>;
};

type Preview = {
  available: boolean;
  port: number | null;
  hostPort: number | null;
};

async function fetchRuntime(id: string): Promise<Runtime> {
  const r = await fetch(`/api/workspace/${id}/runtime`);
  return r.json();
}

async function fetchPreview(id: string): Promise<Preview> {
  const r = await fetch(`/api/workspace/${id}/preview`);
  return r.json();
}

async function postAction(id: string, action: "start" | "stop"): Promise<Runtime> {
  const r = await fetch(`/api/workspace/${id}/runtime`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action }),
  });
  return r.json();
}

type Props = {
  workspaceId: string;
  onPreviewPort: (port: number | null) => void;
};

export function RuntimeBar({ workspaceId, onPreviewPort }: Props) {
  const qc = useQueryClient();
  const { data: rt } = useQuery({
    queryKey: ["ws", workspaceId, "runtime"],
    queryFn: () => fetchRuntime(workspaceId),
    refetchInterval: 5000,
  });

  // Auto-detect a live dev server inside the container by polling
  // /preview. Only polls when the container is running — no point
  // hammering /proc/net/tcp for a stopped sandbox.
  const isRunning = rt?.state === "running";
  const { data: preview } = useQuery({
    queryKey: ["ws", workspaceId, "preview"],
    queryFn: () => fetchPreview(workspaceId),
    refetchInterval: isRunning ? 3000 : false,
    enabled: isRunning,
  });

  const start = useMutation({
    mutationFn: () => postAction(workspaceId, "start"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["ws", workspaceId, "runtime"] }),
  });
  const stop = useMutation({
    mutationFn: () => postAction(workspaceId, "stop"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["ws", workspaceId, "runtime"] }),
  });

  const isDocker = rt?.mode === "docker";
  const state = rt?.state ?? "absent";
  const ports = rt?.ports ?? {};
  const portList = Object.entries(ports).sort(
    ([a], [b]) => Number(a) - Number(b),
  );

  return (
    <div className="flex items-center gap-2 px-3 py-1.5 border-b border-border/50 text-[12px]">
      {isDocker ? (
        <Box className="size-3.5 text-muted-foreground" />
      ) : (
        <Square className="size-3.5 text-muted-foreground" />
      )}

      <span
        className={cn(
          "rounded px-1.5 py-0.5 text-[10px] uppercase tracking-wide",
          state === "running"
            ? "bg-emerald-500/15 text-emerald-400"
            : state === "stopped"
              ? "bg-amber-500/15 text-amber-400"
              : "bg-muted text-muted-foreground",
        )}
      >
        {isDocker ? state : "host"}
      </span>

      <span className="text-muted-foreground truncate">
        {isDocker
          ? state === "running"
            ? "sandbox running"
            : state === "stopped"
              ? "sandbox stopped"
              : "no sandbox yet"
          : rt?.reason === "image_missing"
            ? "docker image not built — run `npm run build:image`"
            : rt?.reason === "docker_unavailable"
              ? "docker not available — running on host"
              : "running on host"}
      </span>

      {/* Auto-detected live preview chip — appears the moment a dev
          server starts listening inside the container. */}
      {preview?.hostPort && preview.port && (
        <button
          onClick={() => onPreviewPort(preview.hostPort!)}
          className="flex items-center gap-1 rounded bg-emerald-500/15 text-emerald-400 px-2 py-0.5 text-[11px] hover:bg-emerald-500/25"
          title={`Live dev server on container port ${preview.port}`}
        >
          <Eye className="size-3" />
          preview :{preview.port}
        </button>
      )}

      {/* Collapsed ports popover — single chip, click to see all 8. */}
      {portList.length > 0 && (
        <PortsPopover portList={portList} onPick={onPreviewPort} />
      )}

      <div className="ml-auto flex items-center gap-1">
        {isDocker && state !== "running" && (
          <button
            onClick={() => start.mutate()}
            disabled={start.isPending}
            className="flex items-center gap-1 rounded px-2 py-0.5 text-[11px] hover:bg-accent disabled:opacity-40"
          >
            {start.isPending ? (
              <Loader2 className="size-3 animate-spin" />
            ) : (
              <RefreshCw className="size-3" />
            )}
            start
          </button>
        )}
        {isDocker && state === "running" && (
          <button
            onClick={() => stop.mutate()}
            disabled={stop.isPending}
            className="flex items-center gap-1 rounded px-2 py-0.5 text-[11px] hover:bg-accent disabled:opacity-40"
          >
            {stop.isPending ? (
              <Loader2 className="size-3 animate-spin" />
            ) : (
              <Square className="size-3" />
            )}
            stop
          </button>
        )}
      </div>
    </div>
  );
}

function PortsPopover({
  portList,
  onPick,
}: {
  portList: [string, number][];
  onPick: (port: number) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    };
    window.addEventListener("mousedown", onClick);
    return () => window.removeEventListener("mousedown", onClick);
  }, [open]);

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1 rounded bg-muted px-2 py-0.5 text-[11px] text-muted-foreground hover:bg-accent"
        title="Container ports exposed to the host"
      >
        ports ({portList.length})
        <ChevronDown className="size-3" />
      </button>
      {open && (
        <div className="absolute top-full left-0 mt-1 z-50 w-56 rounded-md border border-border/60 bg-popover shadow-lg overflow-hidden">
          <div className="px-3 py-1.5 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground border-b border-border/40">
            Open in preview
          </div>
          <div className="max-h-72 overflow-y-auto">
            {portList.map(([cp, hp]) => (
              <button
                key={cp}
                onClick={() => {
                  onPick(hp);
                  setOpen(false);
                }}
                className="flex w-full items-center justify-between gap-2 px-3 py-1.5 text-[12px] hover:bg-accent/60 transition-colors font-mono"
                title={`container :${cp} → host :${hp}`}
              >
                <span>:{cp}</span>
                <span className="text-muted-foreground">→ :{hp}</span>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

type PreviewProps = {
  port: number | null;
  onClose: () => void;
};

export function PreviewIframe({ port, onClose }: PreviewProps) {
  if (!port) {
    return (
      <div className="flex h-full items-center justify-center text-xs text-muted-foreground">
        Click a port chip in the runtime bar to preview a running dev server.
      </div>
    );
  }
  const url = `http://${typeof window !== "undefined" ? window.location.hostname : "localhost"}:${port}`;
  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2 px-3 py-1.5 border-b border-border/50 text-[12px]">
        <span className="font-mono text-muted-foreground">{url}</span>
        <a
          href={url}
          target="_blank"
          rel="noreferrer"
          className="ml-auto flex items-center gap-1 rounded px-2 py-0.5 text-[11px] hover:bg-accent"
        >
          <ExternalLink className="size-3" />
          open
        </a>
        <button
          onClick={onClose}
          className="rounded px-2 py-0.5 text-[11px] hover:bg-accent"
        >
          close
        </button>
      </div>
      <iframe src={url} className="flex-1 w-full bg-white" title="preview" />
    </div>
  );
}
