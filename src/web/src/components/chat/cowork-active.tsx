"use client";

import { useQuery } from "@tanstack/react-query";
import {
  Briefcase,
  ChevronDown,
  ChevronRight,
  Hand,
  ListTodo,
} from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
} from "@/components/ui/dropdown-menu";
import { useProjects } from "@/hooks/use-projects";
import { formatRelativeTime } from "@/lib/utils";

type BridgeSession = {
  session_id: string;
  title: string;
  status: "needs_input" | "working" | "done";
  created_at?: number;
  last_activity_at?: number;
  archived?: boolean;
};

// Claude Code permission modes surfaced with claude.ai Cowork's names.
const PERMISSION_LABEL: Record<string, string> = {
  default: "Manual",
  acceptEdits: "Auto",
  bypassPermissions: "Bypass",
};

/**
 * Project + permission-mode pickers shown under the composer while Cowork
 * is selected (claude.ai parity). Project attaches one of the user's real
 * projects (its instructions ride into the task); permission mode is the
 * approval policy the session runs under.
 */
export function CoworkOptionsRow({
  projectId,
  onProjectChange,
  permission,
  onPermissionChange,
}: {
  projectId: string | null;
  onProjectChange: (id: string | null) => void;
  permission: string;
  onPermissionChange: (mode: string) => void;
}) {
  const { data: projects } = useProjects();
  const selected = (projects ?? []).find((p) => p.id === projectId);
  const pill =
    "flex h-7 items-center gap-1.5 rounded-lg border border-border/50 bg-card/40 px-2.5 text-[12.5px] font-medium text-foreground/85 transition-colors hover:bg-card";

  return (
    <div className="mt-2 flex items-center gap-2 px-1">
      <DropdownMenu>
        <DropdownMenuTrigger render={<button type="button" className={pill} />}>
          <Briefcase className="size-3.5 text-muted-foreground" />
          <span className="max-w-40 truncate">{selected?.name ?? "Project"}</span>
          <ChevronDown className="size-3 text-muted-foreground" />
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="min-w-44">
          {(projects ?? []).length === 0 ? (
            <DropdownMenuItem disabled>No projects yet</DropdownMenuItem>
          ) : (
            (projects ?? []).map((p) => (
              <DropdownMenuItem key={p.id} onClick={() => onProjectChange(p.id)}>
                <span className="truncate">{p.name}</span>
              </DropdownMenuItem>
            ))
          )}
          {selected && (
            <DropdownMenuItem
              onClick={() => onProjectChange(null)}
              className="text-muted-foreground"
            >
              No project
            </DropdownMenuItem>
          )}
        </DropdownMenuContent>
      </DropdownMenu>
      <DropdownMenu>
        <DropdownMenuTrigger render={<button type="button" className={pill} />}>
          <Hand className="size-3.5 text-muted-foreground" />
          {PERMISSION_LABEL[permission] ?? permission}
          <ChevronDown className="size-3 text-muted-foreground" />
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="min-w-36">
          {Object.entries(PERMISSION_LABEL).map(([mode, label]) => (
            <DropdownMenuItem key={mode} onClick={() => onPermissionChange(mode)}>
              {label}
            </DropdownMenuItem>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}

/**
 * "Active" cowork tasks under the home composer (claude.ai parity) —
 * the user's in-flight /code sessions, so toggling Cowork shows what
 * Jarvis is already working on. Clicking a row opens the live session.
 */
export function CoworkActive({ onOpen }: { onOpen: (id: string) => void }) {
  const { data } = useQuery({
    queryKey: ["bridge-sessions"],
    queryFn: async () => {
      const r = await fetch("/api/bridge/v1/sessions");
      if (!r.ok) throw new Error("sessions unavailable");
      return ((await r.json()) as { sessions: BridgeSession[] }).sessions;
    },
    staleTime: 30_000,
    retry: 1,
  });

  const active = (data ?? [])
    .filter((s) => !s.archived && s.status !== "done")
    .sort(
      (a, b) =>
        (b.last_activity_at ?? b.created_at ?? 0) -
        (a.last_activity_at ?? a.created_at ?? 0),
    )
    .slice(0, 6);

  if (active.length === 0) return null;

  return (
    <div className="mx-auto mt-8 w-full max-w-3xl px-4">
      <div className="px-1 pb-1 text-[13px] text-muted-foreground">Active</div>
      <div className="divide-y divide-border/40">
        {active.map((s) => (
          <button
            key={s.session_id}
            type="button"
            onClick={() => onOpen(s.session_id)}
            className="group flex w-full items-center gap-3 px-1 py-3 text-left transition-colors hover:bg-accent/30"
          >
            <span className="relative shrink-0 text-muted-foreground">
              <ListTodo className="size-4" />
              {s.status === "needs_input" && (
                <span className="absolute -right-1 -top-1 size-1.5 rounded-full bg-primary" />
              )}
            </span>
            <span className="min-w-0 flex-1">
              <span className="flex items-center gap-2">
                {s.status === "needs_input" && (
                  <span className="shrink-0 text-[12px] font-medium text-primary">
                    Needs input
                  </span>
                )}
                <span className="truncate text-[14px] text-foreground">
                  {s.title || "Untitled task"}
                </span>
              </span>
              <span className="block text-[12px] text-muted-foreground">
                {formatRelativeTime(s.last_activity_at ?? s.created_at ?? 0)}
              </span>
            </span>
            <ChevronRight className="size-4 shrink-0 text-muted-foreground/50 transition-transform group-hover:translate-x-0.5" />
          </button>
        ))}
      </div>
    </div>
  );
}
