"use client";

import { useQuery } from "@tanstack/react-query";
import { Eye, Loader2 } from "lucide-react";
import type { ViewportPreset } from "@/components/workbench/toolbar";

type Preview = {
  available: boolean;
  port: number | null;
  hostPort: number | null;
};

async function fetchPreview(id: string): Promise<Preview> {
  return (await fetch(`/api/workspace/${id}/preview`)).json();
}

type Props = {
  workspaceId: string;
  iframeKey: number;
  viewport?: ViewportPreset;
};

const VIEWPORT_WIDTH: Record<ViewportPreset, string> = {
  desktop: "100%",
  mobile: "390px",
};

export function PreviewTab({ workspaceId, iframeKey, viewport = "desktop" }: Props) {
  const { data, isLoading } = useQuery({
    queryKey: ["ws", workspaceId, "preview"],
    queryFn: () => fetchPreview(workspaceId),
    refetchInterval: 2000,
  });

  const port = data?.hostPort ?? null;

  if (!port) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 px-6 text-center">
        <div className="flex size-12 items-center justify-center rounded-lg border border-border/60 bg-card/40">
          {isLoading ? (
            <Loader2 className="size-5 animate-spin text-muted-foreground" />
          ) : (
            <Eye className="size-5 text-muted-foreground" />
          )}
        </div>
        <div>
          <p className="text-sm text-foreground/80">No dev server detected yet.</p>
          <p className="mt-1 max-w-md text-xs leading-5 text-muted-foreground">
            Start one in the terminal (e.g. <code className="font-mono">bun run dev</code>) or
            ask Jarvis to scaffold a project from chat. The preview appears here
            automatically once anything starts listening on port 5173 inside the sandbox.
          </p>
        </div>
      </div>
    );
  }

  const url = `http://${typeof window !== "undefined" ? window.location.hostname : "localhost"}:${port}`;
  const isMobile = viewport === "mobile";

  return (
    <div className="flex h-full w-full items-start justify-center overflow-auto bg-muted/20">
      <iframe
        key={`${iframeKey}-${viewport}`}
        src={url}
        className="h-full bg-white"
        style={{
          width: VIEWPORT_WIDTH[viewport],
          minWidth: isMobile ? VIEWPORT_WIDTH[viewport] : undefined,
          maxWidth: isMobile ? VIEWPORT_WIDTH[viewport] : undefined,
        }}
        title="preview"
      />
    </div>
  );
}
