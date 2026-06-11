"use client";

import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Eye, Loader2, Play } from "lucide-react";
import type { ViewportPreset } from "@/components/workbench/toolbar";

type Preview = {
  available: boolean;
  port: number | null;
  hostPort: number | null;
};

async function fetchPreview(id: string): Promise<Preview> {
  return (await fetch(`/api/workspace/${id}/preview`)).json();
}

type AutostartResult = {
  ok: boolean;
  reason?: string;
  devScript?: string;
  patched?: boolean;
};

async function autostart(id: string): Promise<AutostartResult> {
  const r = await fetch(`/api/workspace/${id}/preview/autostart`, {
    method: "POST",
  });
  return r.json();
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

  // Auto-start state. We try once on tab mount when no port exists, then
  // poll handles the rest (the spawn typically takes 5-30s — Preview's
  // own 2s polling picks up the listener as soon as it's up).
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);
  const triedRef = useRef(false);

  const tryAutostart = async () => {
    if (starting) return;
    setStarting(true);
    setStartError(null);
    try {
      const r = await autostart(workspaceId);
      if (!r.ok) {
        setStartError(
          r.reason === "no_package_json"
            ? "No package.json yet — ask Jarvis to scaffold the project first."
            : r.reason === "docker_unavailable"
              ? "Docker not available."
              : `Autostart failed (${r.reason ?? "unknown"}).`,
        );
      }
    } catch (e) {
      setStartError((e as Error).message ?? "autostart failed");
    } finally {
      setStarting(false);
    }
  };

  // Auto-fire once after data has loaded and confirms no port is up.
  useEffect(() => {
    if (triedRef.current) return;
    if (data && !port && !isLoading) {
      triedRef.current = true;
      void tryAutostart();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, port, isLoading]);

  // Self-healing auto-trigger. Once we've fired autostart, if the dev
  // server is still not listening 30s later we assume something
  // crashed and call /heal. The heal endpoint reads dev.log for known
  // signatures (EADDRINUSE, missing modules, uncaught exceptions) and
  // applies the canonical fix without involving the LLM. We cap at 2
  // auto-heals per session to avoid restart loops.
  const healedCountRef = useRef(0);
  const healingRef = useRef(false);
  const [healMessage, setHealMessage] = useState<string | null>(null);
  useEffect(() => {
    if (port) {
      // Server came up — reset for any future crashes.
      healedCountRef.current = 0;
      return;
    }
    if (!triedRef.current || starting) return;
    if (healedCountRef.current >= 2) return;
    const t = setTimeout(async () => {
      if (port || healingRef.current) return;
      healingRef.current = true;
      try {
        const r = await fetch(`/api/workspace/${workspaceId}/heal`, {
          method: "POST",
        });
        if (r.ok) {
          const j = (await r.json()) as {
            acted: boolean;
            signature: string | null;
            details: string;
          };
          if (j.acted) {
            healedCountRef.current += 1;
            setHealMessage(j.details);
          } else {
            setHealMessage(j.details);
          }
        }
      } catch {
        /* swallow — preview polling handles the next cycle */
      } finally {
        healingRef.current = false;
      }
    }, 30_000);
    return () => clearTimeout(t);
  }, [workspaceId, port, starting]);

  if (!port) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 px-6 text-center">
        <div className="flex size-12 items-center justify-center rounded-lg border border-border/60 bg-card/40">
          {isLoading || starting ? (
            <Loader2 className="size-5 animate-spin text-muted-foreground" />
          ) : (
            <Eye className="size-5 text-muted-foreground" />
          )}
        </div>
        <div>
          <p className="text-sm text-foreground/80">
            {starting
              ? "Starting dev server…"
              : healMessage
                ? "Auto-healing dev server…"
                : startError
                  ? "Couldn't start the dev server."
                  : "No dev server detected yet."}
          </p>
          <p className="mt-1 max-w-md text-xs leading-5 text-muted-foreground">
            {starting ? (
              <>Patching the dev script (if needed) and spawning <code className="font-mono">bun run dev</code> on port 5173. Preview should appear within ~10s once it&apos;s up.</>
            ) : healMessage ? (
              healMessage
            ) : startError ? (
              startError
            ) : (
              <>The preview appears here automatically once anything starts listening on port 5173.</>
            )}
          </p>
        </div>
        {!starting && (
          <button
            type="button"
            onClick={() => {
              triedRef.current = true;
              void tryAutostart();
            }}
            className="inline-flex items-center gap-1.5 rounded-md border border-border/60 px-3 py-1.5 text-xs font-medium hover:border-primary/50 hover:bg-primary/5 transition-colors"
          >
            <Play className="size-3.5" />
            {startError ? "Retry start" : "Start dev server"}
          </button>
        )}
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
