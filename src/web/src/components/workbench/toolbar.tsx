"use client";

import { useQuery } from "@tanstack/react-query";
import {
  Eye,
  Code2,
  Database,
  RotateCw,
  ExternalLink,
  Maximize2,
  Minimize2,
  Lock,
  Settings2,
  RectangleHorizontal,
  RectangleVertical,
  PenLine,
  Download,
  GitBranch,
} from "lucide-react";
import Link from "next/link";
import { cn } from "@/lib/utils";

export type WorkbenchTab = "preview" | "code" | "database" | "terminal" | "history" | "settings";
export type ViewportPreset = "desktop" | "mobile";

type Runtime = {
  mode: "docker" | "local";
  state: "running" | "stopped" | "absent";
  ports: Record<string, number>;
};

type Preview = {
  available: boolean;
  port: number | null;
  hostPort: number | null;
};

type Props = {
  workspaceId: string;
  workspaceName: string;
  active: WorkbenchTab;
  onTabChange: (t: WorkbenchTab) => void;
  iframeKey: number;
  onRefresh: () => void;
  fullscreen: boolean;
  onToggleFullscreen: () => void;
  viewport: ViewportPreset;
  onViewportChange: (v: ViewportPreset) => void;
};

async function fetchRuntime(id: string): Promise<Runtime> {
  return (await fetch(`/api/workspace/${id}/runtime`)).json();
}
async function fetchPreview(id: string): Promise<Preview> {
  return (await fetch(`/api/workspace/${id}/preview`)).json();
}

export function WorkbenchToolbar({
  workspaceId,
  workspaceName,
  active,
  onTabChange,
  onRefresh,
  fullscreen,
  onToggleFullscreen,
  viewport,
  onViewportChange,
}: Props) {
  const { data: rt } = useQuery({
    queryKey: ["ws", workspaceId, "runtime"],
    queryFn: () => fetchRuntime(workspaceId),
    refetchInterval: 5000,
  });
  const isRunning = rt?.state === "running";
  const { data: preview } = useQuery({
    queryKey: ["ws", workspaceId, "preview"],
    queryFn: () => fetchPreview(workspaceId),
    refetchInterval: isRunning ? 3000 : false,
    enabled: isRunning,
  });

  const livePort = preview?.hostPort ?? null;
  const previewUrl = livePort
    ? `http://${typeof window !== "undefined" ? window.location.hostname : "localhost"}:${livePort}`
    : null;

  // No auto-switch to the Preview tab when the dev server boots — the
  // Code tab already renders the preview iframe when no file is open
  // (and switches to the editor only when the user clicks a file). So
  // landing on Code shows the preview AND the file tree, which is the
  // intended default.

  return (
    <div className="flex items-center h-11 px-3 border-b border-border/50 bg-background gap-2 overflow-hidden">

      {/* ── LEFT: logo · name · tabs ─────────────────────────────────────── */}
      <div className="flex items-center gap-1.5 shrink-0 min-w-0">

        <Link
          href="/chat"
          title="Back to home"
          className="flex size-6 items-center justify-center rounded-[5px] bg-foreground text-background text-[12px] font-bold leading-none select-none shrink-0 hover:opacity-80 transition-opacity"
        >
          J
        </Link>

        <span className="text-[13px] text-muted-foreground/30 select-none">/</span>

        <span className="text-[13px] font-normal text-foreground/90 truncate max-w-24 shrink">
          {workspaceName}
        </span>
        <Lock className="size-3 text-muted-foreground/40 shrink-0" />
        <RuntimeBadge state={rt?.state ?? "absent"} />

        <div className="flex items-center gap-px ml-2 shrink-0">
          <TabIcon active={active === "preview"} title="Preview" onClick={() => onTabChange("preview")}>
            <Eye className="size-3.5" />
          </TabIcon>
          <TabIcon active={active === "code"} title="Code" onClick={() => onTabChange("code")}>
            <Code2 className="size-3.5" />
          </TabIcon>
          <TabIcon active={active === "database"} title="Database" onClick={() => onTabChange("database")}>
            <Database className="size-3.5" />
          </TabIcon>
          <TabIcon active={active === "terminal"} title="Terminal" onClick={() => onTabChange("terminal")}>
            <PenLine className="size-3.5" />
          </TabIcon>
          <TabIcon active={active === "history"} title="History" onClick={() => onTabChange("history")}>
            <GitBranch className="size-3.5" />
          </TabIcon>
          <TabIcon active={active === "settings"} title="Settings" onClick={() => onTabChange("settings")}>
            <Settings2 className="size-3.5" />
          </TabIcon>
        </div>
      </div>

      {/* ── CENTER: URL pill — flex-1 so it never overlaps left/right ──────── */}
      <div className="flex-1 flex justify-center min-w-0 px-2">
        <div className="flex items-center h-8 pl-3 pr-1.5 rounded-full border border-border bg-muted/40 gap-2 w-full max-w-80">
          <span className={cn(
            "flex-1 truncate font-mono text-[12px]",
            previewUrl ? "text-foreground" : "text-muted-foreground",
          )}>
            {previewUrl ?? "/"}
          </span>
          <div className="flex items-center gap-0.5 shrink-0">
            <UrlBtn title="Refresh" onClick={onRefresh} disabled={!previewUrl}>
              <RotateCw className="size-3.5" />
            </UrlBtn>
            <UrlBtn as="a" href={previewUrl ?? undefined} target="_blank" rel="noreferrer" title="Open in new tab" disabled={!previewUrl}>
              <ExternalLink className="size-3.5" />
            </UrlBtn>
            <UrlBtn
              title={viewport === "desktop" ? "Mobile view" : "Desktop view"}
              onClick={() => onViewportChange(viewport === "desktop" ? "mobile" : "desktop")}
              active={viewport === "mobile"}
              disabled={!previewUrl}
            >
              {viewport === "mobile"
                ? <RectangleVertical className="size-3.5" />
                : <RectangleHorizontal className="size-3.5" />}
            </UrlBtn>
            <UrlBtn title={fullscreen ? "Exit fullscreen" : "Fullscreen"} onClick={onToggleFullscreen} disabled={!previewUrl}>
              {fullscreen ? <Minimize2 className="size-3.5" /> : <Maximize2 className="size-3.5" />}
            </UrlBtn>
          </div>
        </div>
      </div>

      {/* ── RIGHT: Download · GitHub · Share · Publish ───────────────────── */}
      <div className="flex items-center gap-2 shrink-0">
        {/* Download — opens /api/workspace/<id>/zip in a new tab; the
            server returns Content-Disposition: attachment so the browser
            saves the project as <name>-<date>.zip. Excludes node_modules
            / .next / .git / data DBs. */}
        <a
          href={`/api/workspace/${workspaceId}/zip`}
          title="Download project as .zip"
          className="flex size-7 items-center justify-center text-muted-foreground hover:text-foreground transition-colors"
        >
          <Download className="size-4" />
        </a>

        {/* GitHub octocat */}
        <button
          title="GitHub"
          className="flex size-7 items-center justify-center text-muted-foreground hover:text-foreground transition-colors"
        >
          <svg viewBox="0 0 24 24" className="size-4.5 fill-current" aria-hidden>
            <path d="M12 2C6.477 2 2 6.484 2 12.017c0 4.425 2.865 8.18 6.839 9.504.5.092.682-.217.682-.483 0-.237-.008-.868-.013-1.703-2.782.605-3.369-1.343-3.369-1.343-.454-1.158-1.11-1.466-1.11-1.466-.908-.62.069-.608.069-.608 1.003.07 1.531 1.032 1.531 1.032.892 1.53 2.341 1.088 2.91.832.092-.647.35-1.088.636-1.338-2.22-.253-4.555-1.113-4.555-4.951 0-1.093.39-1.988 1.029-2.688-.103-.253-.446-1.272.098-2.65 0 0 .84-.27 2.75 1.026A9.564 9.564 0 0112 6.844c.85.004 1.705.115 2.504.337 1.909-1.296 2.747-1.027 2.747-1.027.546 1.379.202 2.398.1 2.651.64.7 1.028 1.595 1.028 2.688 0 3.848-2.339 4.695-4.566 4.943.359.309.678.92.678 1.855 0 1.338-.012 2.419-.012 2.747 0 .268.18.58.688.482A10.019 10.019 0 0022 12.017C22 6.484 17.522 2 12 2z" />
          </svg>
        </button>

        {/* Share — outlined pill */}
        <button className="text-[12px] font-medium text-foreground/80 hover:text-foreground px-3 py-1.25 rounded-full border border-border/60 hover:bg-accent/40 transition-colors leading-none">
          Share
        </button>

        {/* Publish — solid white pill, black text */}
        <button className="text-[12px] font-semibold bg-white text-black px-3 py-1.25 rounded-full hover:bg-white/90 transition-colors leading-none">
          Publish
        </button>
      </div>

    </div>
  );
}

// ─── Sub-components ────────────────────────────────────────────────────────────

function TabIcon({
  active,
  title,
  onClick,
  children,
}: {
  active: boolean;
  title: string;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      title={title}
      className={cn(
        "flex size-7 items-center justify-center rounded-md transition-colors",
        // Active = colored icon (teal, like bolt), no background
        active
          ? "text-cyan-400"
          : "text-muted-foreground/60 hover:text-foreground",
      )}
    >
      {children}
    </button>
  );
}

function RuntimeBadge({ state }: { state: Runtime["state"] }) {
  if (state === "absent") return null;
  return (
    <span className={cn(
      "rounded px-1.5 py-0.5 text-[9px] uppercase tracking-wider font-medium shrink-0",
      state === "running"
        ? "bg-emerald-500/15 text-emerald-400"
        : "bg-amber-500/15 text-amber-400",
    )}>
      {state}
    </span>
  );
}

type UrlBtnProps = {
  title: string;
  disabled?: boolean;
  active?: boolean;
  children: React.ReactNode;
} & (
  | { as?: "button"; onClick?: () => void; href?: never; target?: never; rel?: never }
  | { as: "a"; href?: string; target?: string; rel?: string; onClick?: never }
);

function UrlBtn(props: UrlBtnProps) {
  const { title, disabled, active, children } = props;
  // Decent tap targets (size-7 ≈ 28px) and visible default color so the
  // bar's icons read as actually clickable. The previous `text-muted-foreground/45`
  // + 12px icons + zero padding made them ghost-icons.
  const cls = cn(
    "flex size-7 items-center justify-center rounded-md transition-colors shrink-0",
    active
      ? "text-foreground bg-muted/60"
      : "text-muted-foreground hover:text-foreground hover:bg-muted/40",
    !disabled && "cursor-pointer",
    disabled && "opacity-30 cursor-not-allowed pointer-events-none",
  );
  if (props.as === "a") {
    return (
      <a className={cls} title={title} href={props.href} target={props.target} rel={props.rel}>
        {children}
      </a>
    );
  }
  return (
    <button className={cls} title={title} onClick={props.onClick} disabled={disabled}>
      {children}
    </button>
  );
}
