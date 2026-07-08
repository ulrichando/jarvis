"use client";

import { useState } from "react";
import { Loader2, Copy, Check, type LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

// ── Type contracts (mirror server response shapes) ──────────────────────

export type Runtime = {
  mode: "docker" | "local";
  reason?: string;
  state: "running" | "stopped" | "absent";
  ports: Record<string, number>;
};

export type EnvDisplay = Record<string, { value: string; masked: boolean }>;

export type WorkspaceMeta = {
  id: string;
  name: string;
  createdAt: number;
  updatedAt: number;
  kind?: "design" | "workbench";
  conversationId?: string;
  customInstructions?: string;
  envVars?: EnvDisplay;
  devCommand?: string;
  deploy?: {
    provider: "vercel";
    teamId?: string;
    projectId?: string;
    projectName?: string;
    latestDeploymentId?: string;
    productionUrl?: string;
  };
};

export type GitStatus = {
  isRepo: boolean;
  branch: string | null;
  dirtyCount: number;
  lastCommit: {
    sha: string;
    shortSha: string;
    subject: string;
    ts: number;
  } | null;
};

export type DbInfo = {
  exists: boolean;
  files: { name: string; bytes: number }[];
  tables: { name: string; rows: number }[];
  schemaError?: string;
};

// Tree endpoint entry shape — shared by Server Functions + File Storage.
export type TreeEntry = { name: string; path: string; type: "file" | "dir" };

// ── Fetchers ────────────────────────────────────────────────────────────

export async function fetchRuntime(id: string): Promise<Runtime> {
  return (await fetch(`/api/workspace/${id}/runtime`)).json();
}
export async function fetchWorkspace(
  id: string,
  revealEnv: string[],
): Promise<WorkspaceMeta | null> {
  const qs = revealEnv.map((k) => `revealEnv=${encodeURIComponent(k)}`).join("&");
  const url = qs ? `/api/workspace/${id}?${qs}` : `/api/workspace/${id}`;
  const r = await fetch(url);
  if (!r.ok) return null;
  const j = await r.json();
  return j.workspace ?? null;
}
export async function fetchGitStatus(id: string): Promise<GitStatus> {
  return (await fetch(`/api/workspace/${id}/git-status`)).json();
}
export async function fetchDbInfo(id: string): Promise<DbInfo> {
  return (await fetch(`/api/workspace/${id}/db-info`)).json();
}
export async function patchWorkspace(id: string, patch: Record<string, unknown>) {
  const r = await fetch(`/api/workspace/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!r.ok) {
    const j = await r.json().catch(() => ({}));
    throw new Error(j.error ?? r.statusText);
  }
  return r.json();
}

// ── Shared primitives ──────────────────────────────────────────────────

export function Section({
  title,
  hint,
  icon,
  tone,
  children,
}: {
  title: string;
  hint?: string;
  icon?: React.ReactNode;
  tone?: "danger";
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-1.5">
        {icon}
        <h3
          className={cn(
            "text-[11px] font-semibold uppercase tracking-wider",
            tone === "danger" ? "text-destructive" : "text-muted-foreground",
          )}
        >
          {title}
        </h3>
      </div>
      {hint && <p className="text-[11.5px] text-muted-foreground">{hint}</p>}
      <div
        className={cn(
          "rounded-lg border bg-card/30 p-4",
          tone === "danger"
            ? "border-destructive/30"
            : "border-border/50",
        )}
      >
        {children}
      </div>
    </div>
  );
}

export function KvRow({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-center justify-between gap-3">
      <span className="text-muted-foreground">{label}</span>
      <div className="text-right">{children}</div>
    </div>
  );
}

export function ActionButton({
  icon,
  label,
  onClick,
  pending,
  tooltip,
}: {
  icon: React.ReactNode;
  label: string;
  onClick: () => void;
  pending?: boolean;
  tooltip?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={pending}
      title={tooltip}
      className="flex items-center gap-1.5 rounded-md border border-border/60 px-3 py-1.5 text-[12px] hover:bg-accent disabled:opacity-40"
    >
      {pending ? <Loader2 className="size-3.5 animate-spin" /> : icon}
      {label}
    </button>
  );
}

export function CopyButton({
  value,
  className,
}: {
  value: string;
  className?: string;
}) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(value);
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        } catch {
          /* clipboard blocked */
        }
      }}
      className={cn(
        "inline-flex items-center gap-1.5 rounded px-1 py-0.5 text-foreground/85 hover:bg-accent",
        className,
      )}
      title="Copy"
    >
      <span className="truncate text-[11.5px]">{value}</span>
      {copied ? (
        <Check className="size-3 text-emerald-500" />
      ) : (
        <Copy className="size-3 text-muted-foreground" />
      )}
    </button>
  );
}

// ── Helpers ────────────────────────────────────────────────────────────

export function formatDate(ts: number): string {
  return new Date(ts).toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

export function relativeTime(ts: number): string {
  const diff = Math.max(0, Date.now() - ts);
  const sec = Math.floor(diff / 1000);
  if (sec < 60) return "just now";
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.floor(hr / 24);
  if (day < 7) return `${day}d ago`;
  return new Date(ts).toLocaleDateString();
}

// ── Stub primitive ────────────────────────────────────────────────────

export function StubSection({
  icon: Icon,
  title,
  what,
  willDo,
  needs,
}: {
  icon: LucideIcon;
  title: string;
  what: string;
  willDo: string[];
  needs: string[];
}) {
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <Icon className="size-4 text-muted-foreground" />
        <h2 className="text-base font-semibold">{title}</h2>
        <span className="rounded bg-amber-500/15 px-1.5 py-0.5 text-[9px] uppercase tracking-wider text-amber-500">
          Coming soon
        </span>
      </div>
      <p className="text-[13px] text-muted-foreground">{what}</p>
      <div className="rounded-lg border border-border/60 bg-card/30 p-4">
        <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
          What this section will do
        </h3>
        <ul className="space-y-1.5 text-[12.5px] text-foreground/85">
          {willDo.map((w, i) => (
            <li key={i} className="flex items-start gap-2">
              <span className="mt-1.5 size-1 shrink-0 rounded-full bg-primary/60" />
              <span>{w}</span>
            </li>
          ))}
        </ul>
      </div>
      <div className="rounded-lg border border-border/40 bg-card/20 p-4">
        <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
          What&apos;s needed to wire it up
        </h3>
        <ul className="space-y-1.5 text-[12px] text-muted-foreground">
          {needs.map((n, i) => (
            <li key={i} className="flex items-start gap-2 font-mono">
              <span className="text-muted-foreground/60">·</span>
              <span>{n}</span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
