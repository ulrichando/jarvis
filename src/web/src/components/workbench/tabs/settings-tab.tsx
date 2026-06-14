"use client";

import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient, useMutation } from "@tanstack/react-query";
import {
  Loader2,
  Play,
  Square,
  Trash2,
  RefreshCw,
  Copy,
  Check,
  Eye,
  EyeOff,
  Plus,
  X,
  Database as DatabaseIcon,
  AlertTriangle,
  RotateCcw,
  Settings as SettingsIcon,
  Globe,
  BarChart3,
  Lock,
  Server,
  Users,
  HardDrive,
  BookOpen,
  Sparkles,
  Archive,
  KeyRound,
  type LucideIcon,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { apiDeleteWorkspace } from "@/lib/workspace/client";
import { cn } from "@/lib/utils";

// ── Type contracts (mirror server response shapes) ──────────────────────

type Runtime = {
  mode: "docker" | "local";
  reason?: string;
  state: "running" | "stopped" | "absent";
  ports: Record<string, number>;
};

type EnvDisplay = Record<string, { value: string; masked: boolean }>;

type WorkspaceMeta = {
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

type GitStatus = {
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

type DbInfo = {
  exists: boolean;
  files: { name: string; bytes: number }[];
  tables: { name: string; rows: number }[];
  schemaError?: string;
};

// ── Fetchers ────────────────────────────────────────────────────────────

async function fetchRuntime(id: string): Promise<Runtime> {
  return (await fetch(`/api/workspace/${id}/runtime`)).json();
}
async function fetchWorkspace(
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
async function fetchGitStatus(id: string): Promise<GitStatus> {
  return (await fetch(`/api/workspace/${id}/git-status`)).json();
}
async function fetchDbInfo(id: string): Promise<DbInfo> {
  return (await fetch(`/api/workspace/${id}/db-info`)).json();
}
async function postRuntime(
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
async function patchWorkspace(id: string, patch: Record<string, unknown>) {
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
async function postCommit(id: string, message: string) {
  const r = await fetch(`/api/workspace/${id}/commit`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
  });
  if (!r.ok) {
    const j = await r.json().catch(() => ({}));
    throw new Error(j.error ?? r.statusText);
  }
  return r.json();
}
async function postClear(id: string) {
  const r = await fetch(`/api/workspace/${id}/clear`, { method: "POST" });
  if (!r.ok) {
    const j = await r.json().catch(() => ({}));
    throw new Error(j.error ?? r.statusText);
  }
  return r.json();
}

// ── Component ────────────────────────────────────────────────────────────

type Props = {
  workspaceId: string;
  workspaceName: string;
};

// All Project Settings sections, grouped. Each entry maps a sidebar
// label to its render component. Sections in `working: true` are
// fully-functional; the rest render an actionable placeholder describing
// what the section will do once wired up. Order mirrors what the user
// asked for ("Project Settings" list).
type SectionId =
  | "general"
  | "domains-hosting"
  | "analytics"
  | "database"
  | "authentication"
  | "server-functions"
  | "secrets"
  | "user-management"
  | "file-storage"
  | "knowledge"
  | "skills"
  | "backups"
  | "danger";

type SectionDef = {
  id: SectionId;
  label: string;
  icon: LucideIcon;
  working: boolean;
};

const SECTIONS: SectionDef[] = [
  { id: "general", label: "General", icon: SettingsIcon, working: true },
  { id: "domains-hosting", label: "Domains & Hosting", icon: Globe, working: true },
  { id: "analytics", label: "Analytics", icon: BarChart3, working: true },
  { id: "database", label: "Database", icon: DatabaseIcon, working: true },
  { id: "authentication", label: "Authentication", icon: Lock, working: true },
  { id: "server-functions", label: "Server Functions", icon: Server, working: true },
  { id: "secrets", label: "Secrets", icon: KeyRound, working: true },
  { id: "user-management", label: "User Management", icon: Users, working: true },
  { id: "file-storage", label: "File Storage", icon: HardDrive, working: true },
  { id: "knowledge", label: "Knowledge", icon: BookOpen, working: true },
  { id: "skills", label: "Skills", icon: Sparkles, working: true },
  { id: "backups", label: "Backups", icon: Archive, working: true },
  { id: "danger", label: "Danger zone", icon: AlertTriangle, working: true },
];

export function SettingsTab({ workspaceId, workspaceName }: Props) {
  const qc = useQueryClient();
  const router = useRouter();
  const [revealEnv, setRevealEnv] = useState<string[]>([]);
  const [active, setActive] = useState<SectionId>("general");

  const { data: ws } = useQuery({
    queryKey: ["ws", workspaceId, "meta", revealEnv],
    queryFn: () => fetchWorkspace(workspaceId, revealEnv),
    refetchOnWindowFocus: false,
  });

  const { data: rt } = useQuery({
    queryKey: ["ws", workspaceId, "runtime"],
    queryFn: () => fetchRuntime(workspaceId),
    refetchInterval: 5000,
  });

  const { data: git } = useQuery({
    queryKey: ["ws", workspaceId, "git"],
    queryFn: () => fetchGitStatus(workspaceId),
    refetchInterval: 8000,
  });

  const { data: dbInfo } = useQuery({
    queryKey: ["ws", workspaceId, "db"],
    queryFn: () => fetchDbInfo(workspaceId),
    refetchInterval: 12000,
  });

  return (
    // Explicit height: the parent uses `flex-1 min-h-0 overflow-hidden`,
    // which gives a definite height in the flex layout — but `h-full`
    // on a flex child of a flex-1 parent doesn't always resolve to a
    // pixel value reliably across all rendering paths (notably under
    // react-resizable-panels). Setting style.height ensures the
    // sidebar + content area always have the right vertical size.
    <div className="flex" style={{ height: "100%" }}>
      <aside className="flex w-56 shrink-0 flex-col border-r border-border/60 bg-sidebar/30 px-2 py-4">
        <div className="px-3 pb-3">
          <h2 className="text-[15px] font-semibold tracking-tight">
            Project Settings
          </h2>
          <p className="mt-0.5 truncate text-[11px] text-muted-foreground">
            {ws?.name ?? workspaceName}
          </p>
        </div>
        <nav className="space-y-px">
          {SECTIONS.map((s) => {
            const isActive = s.id === active;
            return (
              <button
                key={s.id}
                type="button"
                onClick={() => setActive(s.id)}
                className={cn(
                  "flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left text-[12.5px] transition-colors",
                  isActive
                    ? "bg-accent text-foreground"
                    : "text-foreground/85 hover:bg-accent/60",
                )}
              >
                <s.icon className="size-3.5 shrink-0 text-muted-foreground" />
                <span className="flex-1 truncate">{s.label}</span>
                {!s.working && (
                  <span className="rounded bg-muted/60 px-1 py-0 text-[9px] uppercase tracking-wider text-muted-foreground/80">
                    Soon
                  </span>
                )}
              </button>
            );
          })}
        </nav>
      </aside>

      <div className="flex-1 overflow-y-auto px-6 py-6">
        <div className="mx-auto max-w-3xl space-y-6">
          {active === "general" && (
            <>
              <Header
                ws={ws ?? null}
                fallbackName={workspaceName}
                workspaceId={workspaceId}
              />
              <CustomInstructionsSection
                ws={ws ?? null}
                workspaceId={workspaceId}
                onSaved={() =>
                  qc.invalidateQueries({ queryKey: ["ws", workspaceId, "meta"] })
                }
              />
              <DevCommandSection
                ws={ws ?? null}
                workspaceId={workspaceId}
                onSaved={() =>
                  qc.invalidateQueries({ queryKey: ["ws", workspaceId, "meta"] })
                }
              />
              <RuntimeSection
                rt={rt ?? null}
                workspaceId={workspaceId}
                onChanged={() =>
                  qc.invalidateQueries({
                    queryKey: ["ws", workspaceId, "runtime"],
                  })
                }
              />
            </>
          )}

          {active === "secrets" && (
            <EnvVarsSection
              ws={ws ?? null}
              workspaceId={workspaceId}
              revealKeys={revealEnv}
              onToggleReveal={(k) =>
                setRevealEnv((prev) =>
                  prev.includes(k) ? prev.filter((x) => x !== k) : [...prev, k],
                )
              }
              onChanged={() =>
                qc.invalidateQueries({
                  queryKey: ["ws", workspaceId, "meta"],
                })
              }
            />
          )}

          {active === "database" && <DatabaseSection dbInfo={dbInfo ?? null} />}

          {active === "backups" && (
            <BackupsSection
              git={git ?? null}
              workspaceId={workspaceId}
              onChanged={() =>
                qc.invalidateQueries({ queryKey: ["ws", workspaceId, "git"] })
              }
            />
          )}

          {active === "server-functions" && (
            <ServerFunctionsSection workspaceId={workspaceId} />
          )}

          {active === "file-storage" && (
            <FileStorageSection workspaceId={workspaceId} />
          )}

          {active === "danger" && (
            <DangerSection
              workspaceId={workspaceId}
              workspaceName={ws?.name ?? workspaceName}
              onDelete={() => router.replace("/workbench")}
              onCleared={() => {
                qc.invalidateQueries({ queryKey: ["ws", workspaceId] });
              }}
            />
          )}

          {active === "domains-hosting" && (
            <DomainsHostingSection
              ws={ws ?? null}
              workspaceId={workspaceId}
              onChanged={() =>
                qc.invalidateQueries({
                  queryKey: ["ws", workspaceId, "meta"],
                })
              }
              onSwitchToSecrets={() => setActive("secrets")}
            />
          )}

          {active === "analytics" && (
            <AnalyticsSection workspaceId={workspaceId} />
          )}

          {active === "authentication" && (
            <AuthSection
              ws={ws ?? null}
              workspaceId={workspaceId}
              onChanged={() =>
                qc.invalidateQueries({
                  queryKey: ["ws", workspaceId, "meta"],
                })
              }
            />
          )}

          {active === "user-management" && (
            <UserMgmtSection workspaceId={workspaceId} />
          )}

          {active === "knowledge" && (
            <KnowledgeSection workspaceId={workspaceId} />
          )}

          {active === "skills" && (
            <SkillsSection workspaceId={workspaceId} />
          )}
        </div>
      </div>
    </div>
  );
}

// ── Header (name editor + IDs + dates) ──────────────────────────────────

function Header({
  ws,
  fallbackName,
  workspaceId,
}: {
  ws: WorkspaceMeta | null;
  fallbackName: string;
  workspaceId: string;
}) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [draftName, setDraftName] = useState("");
  const name = ws?.name ?? fallbackName;

  const rename = useMutation({
    mutationFn: (next: string) => patchWorkspace(workspaceId, { name: next }),
    onSuccess: () => {
      toast.success("Workspace renamed");
      qc.invalidateQueries({ queryKey: ["ws", workspaceId] });
      qc.invalidateQueries({ queryKey: ["workspace", workspaceId] });
      qc.invalidateQueries({ queryKey: ["workspaces"] });
      setEditing(false);
    },
    onError: (err: Error) => toast.error(`Rename failed: ${err.message}`),
  });

  return (
    <div className="space-y-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          {editing ? (
            <div className="flex items-center gap-2">
              <input
                autoFocus
                value={draftName}
                onChange={(e) => setDraftName(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") rename.mutate(draftName);
                  if (e.key === "Escape") setEditing(false);
                }}
                placeholder={name}
                maxLength={80}
                className="rounded-md border border-border bg-background px-2 py-1 text-base font-semibold outline-none focus:border-primary"
              />
              <button
                type="button"
                onClick={() => rename.mutate(draftName)}
                disabled={rename.isPending || !draftName.trim()}
                className="rounded-md border border-border px-2 py-1 text-[12px] hover:bg-accent disabled:opacity-50"
              >
                Save
              </button>
              <button
                type="button"
                onClick={() => setEditing(false)}
                className="rounded-md border border-border px-2 py-1 text-[12px] hover:bg-accent"
              >
                Cancel
              </button>
            </div>
          ) : (
            <h2
              className="cursor-text text-lg font-semibold hover:opacity-80"
              onClick={() => {
                setDraftName(name);
                setEditing(true);
              }}
              title="Click to rename"
            >
              {name}
            </h2>
          )}
        </div>
      </div>
      <div className="grid grid-cols-2 gap-x-6 gap-y-1.5 text-[12px]">
        <KvRow label="Workspace ID">
          <CopyButton value={workspaceId} className="font-mono" />
        </KvRow>
        <KvRow label="Kind">
          <span className="font-mono">{ws?.kind ?? "design"}</span>
        </KvRow>
        <KvRow label="Created">
          <span>{ws ? formatDate(ws.createdAt) : "—"}</span>
        </KvRow>
        <KvRow label="Updated">
          <span>{ws ? formatDate(ws.updatedAt) : "—"}</span>
        </KvRow>
        {ws?.conversationId && (
          <KvRow label="Conversation">
            <CopyButton value={ws.conversationId} className="font-mono" />
          </KvRow>
        )}
      </div>
    </div>
  );
}

// ── Custom instructions (.cursorrules / CLAUDE.md analog) ───────────────

function CustomInstructionsSection({
  ws,
  workspaceId,
  onSaved,
}: {
  ws: WorkspaceMeta | null;
  workspaceId: string;
  onSaved: () => void;
}) {
  const [draft, setDraft] = useState("");
  const [dirty, setDirty] = useState(false);

  // Sync draft when server-side value changes (and we haven't started editing).
  useEffect(() => {
    if (!dirty) setDraft(ws?.customInstructions ?? "");
  }, [ws?.customInstructions, dirty]);

  const save = useMutation({
    mutationFn: () =>
      patchWorkspace(workspaceId, { customInstructions: draft }),
    onSuccess: () => {
      toast.success("Custom instructions saved");
      setDirty(false);
      onSaved();
    },
    onError: (err: Error) => toast.error(`Save failed: ${err.message}`),
  });

  const len = draft.length;
  const max = 8192;

  return (
    <Section
      title="Custom instructions"
      hint="Workspace-scoped rules for the AI. Appended to every chat turn's system prompt — same role as Cursor's .cursorrules or Claude Code's CLAUDE.md. Keep it tight; 8K char limit."
    >
      <textarea
        value={draft}
        onChange={(e) => {
          setDraft(e.target.value);
          setDirty(true);
        }}
        placeholder={`e.g.\n- Always use server components by default\n- Never use any package whose version is < 1.0\n- Match the existing test style in this repo`}
        rows={8}
        maxLength={max}
        className="w-full resize-y rounded-md border border-border bg-background px-3 py-2 font-mono text-[12px] leading-snug outline-none focus:border-primary"
      />
      <div className="mt-2 flex items-center justify-between text-[11px] text-muted-foreground">
        <span className={cn(len > max * 0.9 && "text-amber-500")}>
          {len.toLocaleString()} / {max.toLocaleString()} chars
        </span>
        {dirty && (
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => {
                setDraft(ws?.customInstructions ?? "");
                setDirty(false);
              }}
              className="rounded-md border border-border px-2 py-1 text-[11px] hover:bg-accent"
            >
              Discard
            </button>
            <button
              type="button"
              onClick={() => save.mutate()}
              disabled={save.isPending}
              className="rounded-md border border-primary/50 bg-primary/10 px-2 py-1 text-[11px] text-primary hover:bg-primary/15 disabled:opacity-50"
            >
              {save.isPending ? "Saving…" : "Save"}
            </button>
          </div>
        )}
      </div>
    </Section>
  );
}

// ── Environment variables ──────────────────────────────────────────────

function EnvVarsSection({
  ws,
  workspaceId,
  revealKeys,
  onToggleReveal,
  onChanged,
}: {
  ws: WorkspaceMeta | null;
  workspaceId: string;
  revealKeys: string[];
  onToggleReveal: (k: string) => void;
  onChanged: () => void;
}) {
  const [newKey, setNewKey] = useState("");
  const [newValue, setNewValue] = useState("");
  const env = ws?.envVars ?? {};
  const keys = Object.keys(env).sort();

  const save = useMutation({
    // The server MERGES envVars and applies removeEnvKeys explicitly, so
    // we only ever send the keys that actually changed. Masked secrets we
    // can't see are preserved server-side instead of being silently
    // dropped (the old "send back everything we have plaintext for"
    // approach wiped every unrevealed secret on any edit).
    mutationFn: (patch: {
      envVars?: Record<string, string>;
      removeEnvKeys?: string[];
    }) => patchWorkspace(workspaceId, patch),
    onSuccess: () => {
      toast.success("Environment variables saved", {
        description:
          "Restart the sandbox from the Runtime section for changes to take effect.",
      });
      onChanged();
    },
    onError: (err: Error) => toast.error(`Save failed: ${err.message}`),
  });

  const addVar = () => {
    const key = newKey.trim().toUpperCase();
    if (!key || !/^[A-Z_][A-Z0-9_]*$/.test(key)) {
      toast.error("Invalid key", {
        description: "Use uppercase letters, digits, and underscores only.",
      });
      return;
    }
    if (newValue.length > 4096) {
      toast.error("Value too long", {
        description: "Environment variable values are capped at 4096 characters.",
      });
      return;
    }
    save.mutate({ envVars: { [key]: newValue } });
    setNewKey("");
    setNewValue("");
  };

  const removeVar = (key: string) => {
    save.mutate({ removeEnvKeys: [key] });
  };

  return (
    <Section
      title="Environment variables"
      hint="Injected into the sandbox container on start. Secret-class values (KEY/TOKEN/SECRET/PASSWORD/DSN/URL) are masked by default — click the eye to reveal. After changing, restart the sandbox to pick up new values."
    >
      <div className="space-y-1.5">
        {keys.length === 0 && (
          <div className="rounded-md border border-dashed border-border/60 px-3 py-3 text-center text-[12px] text-muted-foreground">
            No environment variables yet.
          </div>
        )}
        {keys.map((k) => {
          const e = env[k];
          const revealed = revealKeys.includes(k) || !e.masked;
          return (
            <div
              key={k}
              className="flex items-center gap-2 rounded-md border border-border/40 px-2.5 py-1.5"
            >
              <span className="w-44 truncate font-mono text-[11.5px] font-medium">
                {k}
              </span>
              <span className="flex-1 truncate font-mono text-[11.5px] text-foreground/80">
                {revealed ? e.value : "••••••••"}
              </span>
              {e.masked && (
                <button
                  type="button"
                  onClick={() => onToggleReveal(k)}
                  className="rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground"
                  aria-label={revealed ? "Hide value" : "Reveal value"}
                  title={revealed ? "Hide value" : "Reveal value"}
                >
                  {revealed ? (
                    <EyeOff className="size-3.5" />
                  ) : (
                    <Eye className="size-3.5" />
                  )}
                </button>
              )}
              <button
                type="button"
                onClick={() => removeVar(k)}
                className="rounded p-1 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                aria-label="Remove"
                title="Remove"
              >
                <X className="size-3.5" />
              </button>
            </div>
          );
        })}
      </div>
      <div className="mt-3 flex items-center gap-2">
        <input
          value={newKey}
          onChange={(e) => setNewKey(e.target.value)}
          placeholder="KEY"
          className="w-44 rounded-md border border-border bg-background px-2 py-1.5 font-mono text-[11.5px] outline-none focus:border-primary"
        />
        <input
          value={newValue}
          onChange={(e) => setNewValue(e.target.value)}
          placeholder="value"
          className="flex-1 rounded-md border border-border bg-background px-2 py-1.5 font-mono text-[11.5px] outline-none focus:border-primary"
        />
        <button
          type="button"
          onClick={addVar}
          disabled={save.isPending || !newKey.trim()}
          className="flex items-center gap-1 rounded-md border border-border px-2 py-1.5 text-[11.5px] hover:bg-accent disabled:opacity-50"
        >
          <Plus className="size-3.5" />
          Add
        </button>
      </div>
    </Section>
  );
}

// ── Dev command override ───────────────────────────────────────────────

function DevCommandSection({
  ws,
  workspaceId,
  onSaved,
}: {
  ws: WorkspaceMeta | null;
  workspaceId: string;
  onSaved: () => void;
}) {
  const [draft, setDraft] = useState("");
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    if (!dirty) setDraft(ws?.devCommand ?? "");
  }, [ws?.devCommand, dirty]);

  const save = useMutation({
    mutationFn: () => patchWorkspace(workspaceId, { devCommand: draft }),
    onSuccess: () => {
      toast.success("Dev command saved");
      setDirty(false);
      onSaved();
    },
    onError: (err: Error) => toast.error(`Save failed: ${err.message}`),
  });

  return (
    <Section
      title="Dev command override"
      hint="Replaces `bun run dev` when set. Must bind 0.0.0.0:5173 — that's the only port exposed to the host. Leave blank to use the project's default."
    >
      <div className="flex items-center gap-2">
        <input
          value={draft}
          onChange={(e) => {
            setDraft(e.target.value);
            setDirty(true);
          }}
          placeholder='e.g. "next dev -p 5173 -H 0.0.0.0"'
          className="flex-1 rounded-md border border-border bg-background px-3 py-1.5 font-mono text-[12px] outline-none focus:border-primary"
        />
        {dirty && (
          <>
            <button
              type="button"
              onClick={() => {
                setDraft(ws?.devCommand ?? "");
                setDirty(false);
              }}
              className="rounded-md border border-border px-2 py-1.5 text-[11.5px] hover:bg-accent"
            >
              Discard
            </button>
            <button
              type="button"
              onClick={() => save.mutate()}
              disabled={save.isPending}
              className="rounded-md border border-primary/50 bg-primary/10 px-2 py-1.5 text-[11.5px] text-primary hover:bg-primary/15 disabled:opacity-50"
            >
              {save.isPending ? "Saving…" : "Save"}
            </button>
          </>
        )}
      </div>
    </Section>
  );
}

// ── Sandbox runtime ─────────────────────────────────────────────────────

function RuntimeSection({
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

// ── Database info ──────────────────────────────────────────────────────

function DatabaseSection({ dbInfo }: { dbInfo: DbInfo | null }) {
  return (
    <Section
      title="Database"
      icon={<DatabaseIcon className="size-3.5" />}
      hint="SQLite files in this workspace's data/ directory."
    >
      {!dbInfo || !dbInfo.exists ? (
        <p className="text-[12px] text-muted-foreground">
          No database files in <code className="font-mono">data/</code> yet.
        </p>
      ) : (
        <>
          <div className="space-y-1">
            {dbInfo.files.map((f) => (
              <div
                key={f.name}
                className="flex items-center justify-between rounded-md border border-border/40 px-3 py-1.5 font-mono text-[12px]"
              >
                <span>{f.name}</span>
                <span className="text-muted-foreground">
                  {formatBytes(f.bytes)}
                </span>
              </div>
            ))}
          </div>
          {dbInfo.tables.length > 0 && (
            <div className="mt-3">
              <div className="mb-1 text-[10px] uppercase tracking-wider text-muted-foreground">
                Tables ({dbInfo.tables.length})
              </div>
              <div className="space-y-0.5">
                {dbInfo.tables.map((t) => (
                  <div
                    key={t.name}
                    className="flex items-center justify-between rounded px-2 py-1 font-mono text-[11.5px]"
                  >
                    <span>{t.name}</span>
                    <span className="text-muted-foreground">
                      {t.rows.toLocaleString()} row{t.rows === 1 ? "" : "s"}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
          {dbInfo.schemaError && (
            <p className="mt-2 text-[11px] text-amber-500/85">
              Schema query unavailable: {dbInfo.schemaError}
            </p>
          )}
        </>
      )}
    </Section>
  );
}

// ── Danger zone (clear + delete) ───────────────────────────────────────

function DangerSection({
  workspaceId,
  workspaceName,
  onDelete,
  onCleared,
}: {
  workspaceId: string;
  workspaceName: string;
  onDelete: () => void;
  onCleared: () => void;
}) {
  const clear = useMutation({
    mutationFn: () => postClear(workspaceId),
    onSuccess: () => {
      toast.success("Workspace files cleared");
      onCleared();
    },
    onError: (err: Error) => toast.error(`Clear failed: ${err.message}`),
  });
  const del = useMutation({
    mutationFn: () => apiDeleteWorkspace(workspaceId),
    onSuccess: () => {
      toast.success("Workspace deleted");
      onDelete();
    },
    onError: (err: Error) => toast.error(`Delete failed: ${err.message}`),
  });

  return (
    <Section
      title="Danger zone"
      icon={<AlertTriangle className="size-3.5 text-destructive" />}
      tone="danger"
    >
      <div className="space-y-3">
        <div className="flex items-start justify-between gap-3 rounded-md border border-amber-500/30 bg-amber-500/5 p-3">
          <div className="flex-1 text-[12px]">
            <div className="font-medium">Reset files</div>
            <p className="mt-0.5 text-muted-foreground">
              Deletes everything except the .jarvis settings folder. Useful when
              you want to regenerate from scratch.
            </p>
          </div>
          <button
            type="button"
            onClick={() => {
              if (
                confirm(
                  `Clear all files in "${workspaceName}"? Settings (brand, etc.) survive.`,
                )
              )
                clear.mutate();
            }}
            disabled={clear.isPending}
            className="shrink-0 rounded-md border border-amber-500/50 px-3 py-1.5 text-[11.5px] text-amber-500 hover:bg-amber-500/10 disabled:opacity-50"
          >
            {clear.isPending ? "Clearing…" : "Reset"}
          </button>
        </div>
        <div className="flex items-start justify-between gap-3 rounded-md border border-destructive/30 bg-destructive/5 p-3">
          <div className="flex-1 text-[12px]">
            <div className="font-medium text-destructive">Delete workspace</div>
            <p className="mt-0.5 text-muted-foreground">
              Removes the workspace, its files, and the sandbox container. Cannot
              be undone.
            </p>
          </div>
          <button
            type="button"
            onClick={() => {
              if (
                confirm(
                  `Delete workspace "${workspaceName}"? Files and the sandbox container will be removed.`,
                )
              )
                del.mutate();
            }}
            disabled={del.isPending}
            className="shrink-0 rounded-md border border-destructive/50 px-3 py-1.5 text-[11.5px] text-destructive hover:bg-destructive/10 disabled:opacity-50"
          >
            <span className="inline-flex items-center gap-1.5">
              <Trash2 className="size-3.5" />
              {del.isPending ? "Deleting…" : "Delete"}
            </span>
          </button>
        </div>
      </div>
    </Section>
  );
}

// ── Shared primitives ──────────────────────────────────────────────────

function Section({
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

function KvRow({
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

function ActionButton({
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

function CopyButton({
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

function formatDate(ts: number): string {
  return new Date(ts).toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

function relativeTime(ts: number): string {
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

// ── Backups (git-backed) ──────────────────────────────────────────────

type CommitInfo = {
  sha: string;
  shortSha: string;
  subject: string;
  ts: number;
};

async function fetchCommits(id: string): Promise<{ commits: CommitInfo[] }> {
  const r = await fetch(`/api/workspace/${id}/commit`);
  if (!r.ok) return { commits: [] };
  return r.json();
}

async function postRestore(id: string, sha: string) {
  const r = await fetch(`/api/workspace/${id}/commit`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action: "restore", sha }),
  });
  if (!r.ok) {
    const j = await r.json().catch(() => ({}));
    throw new Error(j.error ?? r.statusText);
  }
  return r.json();
}

function BackupsSection({
  git,
  workspaceId,
  onChanged,
}: {
  git: GitStatus | null;
  workspaceId: string;
  onChanged: () => void;
}) {
  const qc = useQueryClient();
  const [msg, setMsg] = useState("");
  const { data } = useQuery({
    queryKey: ["ws", workspaceId, "commits"],
    queryFn: () => fetchCommits(workspaceId),
    refetchOnWindowFocus: false,
  });
  const commit = useMutation({
    mutationFn: () => postCommit(workspaceId, msg.trim() || "manual snapshot"),
    onSuccess: (j: { commit: { shortSha: string } | null }) => {
      if (j.commit) toast.success(`Snapshot ${j.commit.shortSha} created`);
      else toast.message("Nothing to snapshot — working tree is clean");
      setMsg("");
      qc.invalidateQueries({ queryKey: ["ws", workspaceId, "commits"] });
      onChanged();
    },
    onError: (err: Error) => toast.error(`Snapshot failed: ${err.message}`),
  });
  const restore = useMutation({
    mutationFn: (sha: string) => postRestore(workspaceId, sha),
    onSuccess: (_data, sha) => {
      toast.success(`Restored to ${sha.slice(0, 7)}`);
      qc.invalidateQueries({ queryKey: ["ws", workspaceId] });
    },
    onError: (err: Error) => toast.error(`Restore failed: ${err.message}`),
  });

  const commits = data?.commits ?? [];

  return (
    <>
      <Section
        title="Snapshot now"
        icon={<Archive className="size-3.5" />}
        hint="Each workspace is a git repo. The AI auto-snapshots after every successful turn; this is for manual checkpoints."
      >
        <div className="flex items-center gap-2">
          <input
            value={msg}
            onChange={(e) => setMsg(e.target.value)}
            placeholder="What's this snapshot for?"
            className="flex-1 rounded-md border border-border bg-background px-3 py-1.5 text-[12px] outline-none focus:border-primary"
          />
          <button
            type="button"
            onClick={() => commit.mutate()}
            disabled={commit.isPending || (git?.dirtyCount ?? 0) === 0}
            className="rounded-md border border-primary/50 bg-primary/10 px-3 py-1.5 text-[11.5px] text-primary hover:bg-primary/15 disabled:opacity-50"
            title={
              (git?.dirtyCount ?? 0) === 0
                ? "Nothing to snapshot — working tree clean"
                : "Create a manual snapshot"
            }
          >
            {commit.isPending ? "Saving…" : "Snapshot"}
          </button>
        </div>
        {git && (
          <div className="mt-2 text-[11.5px] text-muted-foreground">
            {git.branch && <>Branch <span className="font-mono">{git.branch}</span> · </>}
            {git.dirtyCount === 0
              ? "Working tree clean"
              : `${git.dirtyCount} pending change${git.dirtyCount === 1 ? "" : "s"}`}
          </div>
        )}
      </Section>

      <Section
        title="Snapshot history"
        hint="Most recent first. Click Restore to reset the workspace to a prior snapshot — this discards uncommitted changes; snapshot first if you want to keep them."
      >
        {commits.length === 0 ? (
          <p className="text-[12px] text-muted-foreground">
            No snapshots yet. The AI creates one automatically after each
            successful turn.
          </p>
        ) : (
          <div className="space-y-1">
            {commits.map((c) => (
              <div
                key={c.sha}
                className="flex items-center gap-3 rounded-md border border-border/40 px-3 py-2 text-[12px]"
              >
                <span className="font-mono text-[11px] text-muted-foreground">
                  {c.shortSha}
                </span>
                <span className="flex-1 truncate">{c.subject}</span>
                <span className="whitespace-nowrap text-[11px] text-muted-foreground">
                  {relativeTime(c.ts)}
                </span>
                <button
                  type="button"
                  onClick={() => {
                    if (
                      confirm(
                        `Restore to "${c.subject}" (${c.shortSha})? Uncommitted changes will be lost.`,
                      )
                    )
                      restore.mutate(c.sha);
                  }}
                  disabled={restore.isPending}
                  className="rounded border border-border/60 px-2 py-0.5 text-[11px] hover:bg-accent disabled:opacity-50"
                >
                  Restore
                </button>
              </div>
            ))}
          </div>
        )}
      </Section>
    </>
  );
}

// ── Server Functions (lists app/api/* routes) ─────────────────────────

type TreeEntry = { name: string; path: string; type: "file" | "dir" };

async function listAllRoutes(workspaceId: string): Promise<string[]> {
  // Walk app/api recursively. Server Functions = files named route.ts /
  // route.tsx / route.js inside app/api/.
  async function walk(rel: string): Promise<string[]> {
    const url = `/api/workspace/${workspaceId}/tree?path=${encodeURIComponent(rel)}`;
    const r = await fetch(url);
    if (!r.ok) return [];
    const j: { entries?: TreeEntry[] } = await r.json();
    const out: string[] = [];
    for (const e of j.entries ?? []) {
      if (e.type === "dir") {
        out.push(...(await walk(e.path)));
      } else if (/^route\.(ts|tsx|js|mjs)$/.test(e.name)) {
        out.push(e.path);
      }
    }
    return out;
  }
  return walk("app/api");
}

function ServerFunctionsSection({
  workspaceId,
}: {
  workspaceId: string;
}) {
  const { data, isLoading, refetch } = useQuery({
    queryKey: ["ws", workspaceId, "routes"],
    queryFn: () => listAllRoutes(workspaceId),
    refetchOnWindowFocus: false,
  });

  // Convert "app/api/users/[id]/route.ts" → "/api/users/[id]" for display.
  const fnList = useMemo(() => {
    const items = (data ?? []).map((p) => {
      const route = "/" + p.replace(/^app\//, "").replace(/\/route\.[a-z]+$/i, "");
      return { file: p, route };
    });
    items.sort((a, b) => a.route.localeCompare(b.route));
    return items;
  }, [data]);

  return (
    <Section
      title="Server functions"
      icon={<Server className="size-3.5" />}
      hint="Every app/api/**/route.{ts,tsx,js,mjs} file is a server function. Click a row to open it in the Code tab."
    >
      <div className="mb-2 flex items-center justify-between text-[11px] text-muted-foreground">
        <span>{fnList.length} function{fnList.length === 1 ? "" : "s"}</span>
        <button
          type="button"
          onClick={() => refetch()}
          className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 hover:bg-accent"
        >
          <RefreshCw className="size-3" />
          Refresh
        </button>
      </div>
      {isLoading ? (
        <p className="text-[12px] text-muted-foreground">Loading…</p>
      ) : fnList.length === 0 ? (
        <p className="text-[12px] text-muted-foreground">
          No server functions yet. Once the AI creates files under{" "}
          <code className="font-mono">app/api/</code>, they show up here.
        </p>
      ) : (
        <div className="space-y-1">
          {fnList.map((f) => (
            <div
              key={f.file}
              className="flex items-center justify-between rounded-md border border-border/40 px-3 py-1.5 font-mono text-[11.5px]"
            >
              <span className="truncate">
                <span className="text-foreground">{f.route}</span>
              </span>
              <span className="text-[10.5px] text-muted-foreground">
                {f.file}
              </span>
            </div>
          ))}
        </div>
      )}
    </Section>
  );
}

// ── File Storage (top-level files + sizes) ────────────────────────────

type FileStat = { name: string; bytes: number; type: "file" | "dir" };

async function fetchTopLevel(workspaceId: string): Promise<FileStat[]> {
  const r = await fetch(`/api/workspace/${workspaceId}/tree?path=`);
  if (!r.ok) return [];
  const j: { entries?: TreeEntry[] } = await r.json();
  // Tree endpoint doesn't expose sizes directly; show the top-level
  // entries with type. A future iteration can add a `?withSizes=1`
  // option to the tree endpoint to populate `bytes`.
  return (j.entries ?? []).map((e) => ({
    name: e.name,
    bytes: 0,
    type: e.type,
  }));
}

function FileStorageSection({
  workspaceId,
}: {
  workspaceId: string;
}) {
  const { data: entries, isLoading, refetch } = useQuery({
    queryKey: ["ws", workspaceId, "top-level"],
    queryFn: () => fetchTopLevel(workspaceId),
    refetchOnWindowFocus: false,
  });

  const dirs = (entries ?? []).filter((e) => e.type === "dir");
  const files = (entries ?? []).filter((e) => e.type === "file");

  return (
    <Section
      title="File storage"
      icon={<HardDrive className="size-3.5" />}
      hint="Workspace filesystem at /workspace inside the sandbox. The Code tab is the full file browser; this surface shows the top level so you can see project shape at a glance."
    >
      <div className="mb-2 flex items-center justify-between text-[11px] text-muted-foreground">
        <span>
          {dirs.length} folder{dirs.length === 1 ? "" : "s"} ·{" "}
          {files.length} file{files.length === 1 ? "" : "s"}
        </span>
        <button
          type="button"
          onClick={() => refetch()}
          className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 hover:bg-accent"
        >
          <RefreshCw className="size-3" />
          Refresh
        </button>
      </div>
      {isLoading ? (
        <p className="text-[12px] text-muted-foreground">Loading…</p>
      ) : (entries?.length ?? 0) === 0 ? (
        <p className="text-[12px] text-muted-foreground">
          Workspace is empty.
        </p>
      ) : (
        <div className="grid grid-cols-2 gap-1.5">
          {[...dirs, ...files].map((e) => (
            <div
              key={e.name}
              className="flex items-center gap-2 rounded-md border border-border/40 px-2.5 py-1.5 font-mono text-[11.5px]"
            >
              <span
                className={cn(
                  "shrink-0 rounded px-1 py-0 text-[9px] uppercase tracking-wider",
                  e.type === "dir"
                    ? "bg-primary/15 text-primary"
                    : "bg-muted text-muted-foreground",
                )}
              >
                {e.type === "dir" ? "dir" : "file"}
              </span>
              <span className="truncate">{e.name}</span>
            </div>
          ))}
        </div>
      )}
    </Section>
  );
}

// ── Domains & Hosting (Vercel) ─────────────────────────────────────────

type DeploymentRow = {
  uid: string;
  url: string;
  state: string;
  createdAt: number;
  target?: string | null;
  inspectorUrl?: string;
};

type DeploymentsResponse = {
  provider: "vercel" | null;
  configured: boolean;
  deployments: DeploymentRow[];
  hint?: string;
  error?: string;
};

type DomainRow = {
  name: string;
  verified: boolean;
  verification?: Array<{ type: string; domain: string; value: string }>;
};

type DomainsResponse = {
  configured: boolean;
  domains: DomainRow[];
  error?: string;
};

async function fetchDeployments(id: string): Promise<DeploymentsResponse> {
  const r = await fetch(`/api/workspace/${id}/deploy`);
  return r.json();
}

async function postDeploy(
  id: string,
  target: "production" | "preview" = "production",
): Promise<{ deployment?: DeploymentRow; error?: string; message?: string }> {
  const r = await fetch(`/api/workspace/${id}/deploy`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ target }),
  });
  return r.json();
}

async function fetchDomainsList(id: string): Promise<DomainsResponse> {
  const r = await fetch(`/api/workspace/${id}/domains`);
  return r.json();
}

async function addDomainReq(id: string, domain: string) {
  const r = await fetch(`/api/workspace/${id}/domains`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ domain }),
  });
  if (!r.ok) {
    const j = await r.json().catch(() => ({}));
    throw new Error(j.message ?? j.error ?? r.statusText);
  }
  return r.json();
}

async function removeDomainReq(id: string, domain: string) {
  const r = await fetch(
    `/api/workspace/${id}/domains?domain=${encodeURIComponent(domain)}`,
    { method: "DELETE" },
  );
  if (!r.ok) {
    const j = await r.json().catch(() => ({}));
    throw new Error(j.message ?? j.error ?? r.statusText);
  }
  return r.json();
}

function DomainsHostingSection({
  ws,
  workspaceId,
  onChanged,
  onSwitchToSecrets,
}: {
  ws: WorkspaceMeta | null;
  workspaceId: string;
  onChanged: () => void;
  onSwitchToSecrets: () => void;
}) {
  const qc = useQueryClient();
  const hasToken =
    ws?.envVars && Object.keys(ws.envVars).includes("VERCEL_TOKEN");

  const { data: dep } = useQuery({
    queryKey: ["ws", workspaceId, "deploy"],
    queryFn: () => fetchDeployments(workspaceId),
    refetchInterval: 15000,
    enabled: !!hasToken,
  });

  const { data: domsData } = useQuery({
    queryKey: ["ws", workspaceId, "domains"],
    queryFn: () => fetchDomainsList(workspaceId),
    refetchInterval: 15000,
    enabled: !!hasToken && dep?.configured === true,
  });

  const deploy = useMutation({
    mutationFn: (target: "production" | "preview") =>
      postDeploy(workspaceId, target),
    onSuccess: (r) => {
      if (r.deployment) {
        toast.success("Deploy started", {
          description: r.deployment.url,
        });
        qc.invalidateQueries({ queryKey: ["ws", workspaceId, "deploy"] });
        qc.invalidateQueries({ queryKey: ["ws", workspaceId, "meta"] });
        onChanged();
      } else if (r.error) {
        toast.error(`Deploy failed: ${r.message ?? r.error}`);
      }
    },
    onError: (err: Error) => toast.error(`Deploy failed: ${err.message}`),
  });

  const addDom = useMutation({
    mutationFn: (domain: string) => addDomainReq(workspaceId, domain),
    onSuccess: () => {
      toast.success("Domain added — verify the DNS records to activate");
      qc.invalidateQueries({ queryKey: ["ws", workspaceId, "domains"] });
    },
    onError: (err: Error) => toast.error(`Add failed: ${err.message}`),
  });

  const remDom = useMutation({
    mutationFn: (domain: string) => removeDomainReq(workspaceId, domain),
    onSuccess: () => {
      toast.success("Domain removed");
      qc.invalidateQueries({ queryKey: ["ws", workspaceId, "domains"] });
    },
    onError: (err: Error) => toast.error(`Remove failed: ${err.message}`),
  });

  const [newDomain, setNewDomain] = useState("");

  // ── Render ────────────────────────────────────────────────────────────

  if (!hasToken) {
    return (
      <Section
        title="Domains & Hosting"
        icon={<Globe className="size-3.5" />}
        hint="Vercel is the first deploy target. Add VERCEL_TOKEN to Secrets to enable."
      >
        <div className="rounded-md border border-amber-500/30 bg-amber-500/5 p-4">
          <h4 className="text-[13px] font-medium">Connect Vercel</h4>
          <ol className="mt-2 space-y-1.5 text-[12px] text-foreground/85">
            <li>
              1. Open{" "}
              <a
                href="https://vercel.com/account/tokens"
                target="_blank"
                rel="noreferrer"
                className="text-primary underline"
              >
                vercel.com/account/tokens
              </a>{" "}
              and create a token with <code className="font-mono">Full Account</code> scope.
            </li>
            <li>
              2. Copy the token, then go to{" "}
              <button
                type="button"
                onClick={onSwitchToSecrets}
                className="text-primary underline"
              >
                Secrets
              </button>{" "}
              and add it as <code className="font-mono">VERCEL_TOKEN</code>.
            </li>
            <li>3. Come back here — Deploy + Domains will light up.</li>
          </ol>
          <p className="mt-3 text-[11.5px] text-muted-foreground">
            Multi-provider support (Netlify, Cloudflare Pages, Fly.io,
            self-hosted) is on the roadmap.
          </p>
        </div>
      </Section>
    );
  }

  const productionUrl = ws?.deploy?.productionUrl
    ? `https://${ws.deploy.productionUrl}`
    : null;
  const latestState = dep?.deployments?.[0]?.state;

  return (
    <>
      <Section
        title="Production"
        icon={<Globe className="size-3.5" />}
        hint={`Deploys this workspace to Vercel${ws?.deploy?.projectName ? ` as project "${ws.deploy.projectName}"` : ""}.`}
      >
        <div className="grid grid-cols-2 gap-x-6 gap-y-1.5 text-[12px]">
          <KvRow label="Provider">
            <span className="font-mono">vercel</span>
          </KvRow>
          <KvRow label="Project">
            <span className="font-mono">
              {ws?.deploy?.projectName ?? "(creates on first deploy)"}
            </span>
          </KvRow>
          <KvRow label="Production URL">
            {productionUrl ? (
              <a
                href={productionUrl}
                target="_blank"
                rel="noreferrer"
                className="font-mono text-primary hover:underline"
              >
                {ws?.deploy?.productionUrl}
              </a>
            ) : (
              <span className="text-muted-foreground">—</span>
            )}
          </KvRow>
          <KvRow label="Latest state">
            {latestState ? (
              <span
                className={cn(
                  "rounded px-1.5 py-0.5 text-[10px] uppercase tracking-wider",
                  latestState === "READY"
                    ? "bg-emerald-500/15 text-emerald-400"
                    : latestState === "ERROR" || latestState === "CANCELED"
                      ? "bg-destructive/15 text-destructive"
                      : "bg-amber-500/15 text-amber-400",
                )}
              >
                {latestState.toLowerCase()}
              </span>
            ) : (
              <span className="text-muted-foreground">—</span>
            )}
          </KvRow>
        </div>
        <div className="mt-3 flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => deploy.mutate("production")}
            disabled={deploy.isPending}
            className="flex items-center gap-1.5 rounded-md border border-primary/50 bg-primary/10 px-3 py-1.5 text-[12px] text-primary hover:bg-primary/15 disabled:opacity-50"
          >
            {deploy.isPending ? (
              <Loader2 className="size-3.5 animate-spin" />
            ) : (
              <Globe className="size-3.5" />
            )}
            Deploy to production
          </button>
          <button
            type="button"
            onClick={() => deploy.mutate("preview")}
            disabled={deploy.isPending}
            className="flex items-center gap-1.5 rounded-md border border-border/60 px-3 py-1.5 text-[12px] hover:bg-accent disabled:opacity-50"
          >
            Deploy preview
          </button>
        </div>
        {dep?.error && (
          <p className="mt-2 text-[11.5px] text-destructive/85">
            Vercel API: {dep.error}
          </p>
        )}
      </Section>

      <Section title="Recent deployments">
        {!dep?.deployments?.length ? (
          <p className="text-[12px] text-muted-foreground">
            No deployments yet.
          </p>
        ) : (
          <div className="space-y-1">
            {dep.deployments.map((d) => (
              <a
                key={d.uid}
                href={`https://${d.url}`}
                target="_blank"
                rel="noreferrer"
                className="flex items-center gap-3 rounded-md border border-border/40 px-3 py-2 text-[12px] hover:bg-accent/40"
              >
                <span
                  className={cn(
                    "shrink-0 rounded px-1.5 py-0 text-[10px] uppercase tracking-wider",
                    d.state === "READY"
                      ? "bg-emerald-500/15 text-emerald-400"
                      : d.state === "ERROR" || d.state === "CANCELED"
                        ? "bg-destructive/15 text-destructive"
                        : "bg-amber-500/15 text-amber-400",
                  )}
                >
                  {d.state.toLowerCase()}
                </span>
                <span className="flex-1 truncate font-mono">{d.url}</span>
                <span className="shrink-0 text-[10.5px] uppercase tracking-wider text-muted-foreground/70">
                  {d.target ?? "production"}
                </span>
                <span className="shrink-0 text-[11px] text-muted-foreground">
                  {relativeTime(d.createdAt)}
                </span>
              </a>
            ))}
          </div>
        )}
      </Section>

      <Section
        title="Custom domains"
        hint="Domain verification happens via DNS — Vercel returns the records you need to add."
      >
        {!dep?.configured ? (
          <p className="text-[12px] text-muted-foreground">
            Run a first deploy to initialize the project, then add domains.
          </p>
        ) : (
          <>
            <div className="space-y-1">
              {(domsData?.domains ?? []).length === 0 ? (
                <p className="text-[12px] text-muted-foreground">
                  No custom domains yet — using the generated{" "}
                  <code className="font-mono">.vercel.app</code> URL.
                </p>
              ) : (
                domsData!.domains.map((d) => (
                  <div
                    key={d.name}
                    className="rounded-md border border-border/40 px-3 py-2 text-[12px]"
                  >
                    <div className="flex items-center gap-3">
                      <span className="flex-1 truncate font-mono">
                        {d.name}
                      </span>
                      <span
                        className={cn(
                          "rounded px-1.5 py-0 text-[10px] uppercase tracking-wider",
                          d.verified
                            ? "bg-emerald-500/15 text-emerald-400"
                            : "bg-amber-500/15 text-amber-400",
                        )}
                      >
                        {d.verified ? "verified" : "pending DNS"}
                      </span>
                      <button
                        type="button"
                        onClick={() => {
                          if (confirm(`Remove ${d.name}?`))
                            remDom.mutate(d.name);
                        }}
                        disabled={remDom.isPending}
                        className="rounded p-1 text-muted-foreground hover:bg-destructive/10 hover:text-destructive disabled:opacity-50"
                        aria-label="Remove domain"
                      >
                        <X className="size-3.5" />
                      </button>
                    </div>
                    {!d.verified &&
                      d.verification &&
                      d.verification.length > 0 && (
                        <div className="mt-2 rounded bg-muted/40 p-2 font-mono text-[10.5px]">
                          <div className="mb-1 text-[9.5px] uppercase tracking-wider text-muted-foreground/80">
                            Add this DNS record
                          </div>
                          {d.verification.slice(0, 1).map((v, i) => (
                            <div key={i}>
                              {v.type} · {v.domain} · {v.value}
                            </div>
                          ))}
                        </div>
                      )}
                  </div>
                ))
              )}
            </div>
            <div className="mt-3 flex items-center gap-2">
              <input
                value={newDomain}
                onChange={(e) => setNewDomain(e.target.value)}
                placeholder="example.com"
                className="flex-1 rounded-md border border-border bg-background px-3 py-1.5 font-mono text-[12px] outline-none focus:border-primary"
              />
              <button
                type="button"
                onClick={() => {
                  const d = newDomain.trim().toLowerCase();
                  if (!d) return;
                  // Fail fast in the UI instead of round-tripping to the
                  // server's stricter validator (matches it: per-label,
                  // alphabetic TLD, no leading/trailing hyphen).
                  const ok =
                    d.length <= 253 &&
                    d.split(".").length >= 2 &&
                    /^[a-z]{2,}$/.test(d.split(".").pop() ?? "") &&
                    d
                      .split(".")
                      .every(
                        (l) =>
                          /^[a-z0-9-]{1,63}$/.test(l) &&
                          !l.startsWith("-") &&
                          !l.endsWith("-"),
                      );
                  if (!ok) {
                    toast.error("Invalid domain", {
                      description: "Enter a valid hostname, e.g. example.com",
                    });
                    return;
                  }
                  addDom.mutate(d);
                  setNewDomain("");
                }}
                disabled={addDom.isPending || !newDomain.trim()}
                className="rounded-md border border-border px-3 py-1.5 text-[11.5px] hover:bg-accent disabled:opacity-50"
              >
                Add
              </button>
            </div>
            {domsData?.error && (
              <p className="mt-2 text-[11.5px] text-destructive/85">
                Vercel API: {domsData.error}
              </p>
            )}
          </>
        )}
      </Section>
    </>
  );
}

// ── Knowledge ─────────────────────────────────────────────────────────

type KnowledgeDoc = {
  name: string;
  bytes: number;
  updatedAt: number;
  enabled: boolean;
};

function KnowledgeSection({ workspaceId }: { workspaceId: string }) {
  const qc = useQueryClient();
  const [newName, setNewName] = useState("");
  const [newContent, setNewContent] = useState("");

  const { data, isLoading } = useQuery({
    queryKey: ["ws", workspaceId, "knowledge"],
    queryFn: async () => {
      const r = await fetch(`/api/workspace/${workspaceId}/knowledge`);
      return (await r.json()) as { docs: KnowledgeDoc[] };
    },
    refetchOnWindowFocus: false,
  });

  const upload = useMutation({
    mutationFn: async ({ name, content }: { name: string; content: string }) => {
      const r = await fetch(`/api/workspace/${workspaceId}/knowledge`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, content }),
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(j.error ?? r.statusText);
      }
      return r.json();
    },
    onSuccess: () => {
      toast.success("Document added");
      qc.invalidateQueries({ queryKey: ["ws", workspaceId, "knowledge"] });
    },
    onError: (err: Error) => toast.error(`Add failed: ${err.message}`),
  });

  const toggle = useMutation({
    mutationFn: async ({
      name,
      enabled,
    }: {
      name: string;
      enabled: boolean;
    }) => {
      const r = await fetch(`/api/workspace/${workspaceId}/knowledge`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, enabled }),
      });
      if (!r.ok) throw new Error(r.statusText);
      return r.json();
    },
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: ["ws", workspaceId, "knowledge"] });
      toast.success(vars.enabled ? "Document enabled" : "Document disabled");
    },
    onError: (e: Error) => toast.error(`Update failed: ${e.message}`),
  });

  const remove = useMutation({
    mutationFn: async (name: string) => {
      const r = await fetch(
        `/api/workspace/${workspaceId}/knowledge?name=${encodeURIComponent(name)}`,
        { method: "DELETE" },
      );
      if (!r.ok) throw new Error(r.statusText);
      return r.json();
    },
    onSuccess: () => {
      toast.success("Document removed");
      qc.invalidateQueries({ queryKey: ["ws", workspaceId, "knowledge"] });
    },
  });

  const handleFiles = async (files: FileList | null) => {
    if (!files) return;
    for (const f of Array.from(files)) {
      const text = await f.text();
      upload.mutate({ name: f.name, content: text });
    }
  };

  return (
    <>
      <Section
        title="Knowledge"
        icon={<BookOpen className="size-3.5" />}
        hint="Reference docs the AI reads on every chat turn in this workspace. Each enabled doc is appended to the system prompt (truncated to 4K chars). For brand guidelines, API contracts, project conventions, etc."
      >
        <label className="flex flex-col items-center justify-center gap-2 rounded-md border-2 border-dashed border-border/60 bg-card/30 px-6 py-8 text-center cursor-pointer hover:border-border">
          <BookOpen className="size-5 text-muted-foreground" />
          <div className="text-[12.5px] font-medium">
            Drop a .md / .txt / .json file
          </div>
          <div className="text-[11px] text-muted-foreground">
            or click to browse · max 1MB per file
          </div>
          <input
            type="file"
            accept=".md,.txt,.json,.yaml,.yml,.csv"
            multiple
            className="hidden"
            onChange={(e) => handleFiles(e.target.files)}
          />
        </label>
        <details className="mt-3 text-[12px]">
          <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
            Or paste text directly
          </summary>
          <div className="mt-2 space-y-2">
            <input
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="filename.md"
              className="w-full rounded-md border border-border bg-background px-2 py-1 font-mono text-[11.5px] outline-none focus:border-primary"
            />
            <textarea
              value={newContent}
              onChange={(e) => setNewContent(e.target.value)}
              placeholder="paste content here…"
              rows={6}
              className="w-full resize-y rounded-md border border-border bg-background px-2 py-1 font-mono text-[11.5px] leading-snug outline-none focus:border-primary"
            />
            <button
              type="button"
              onClick={() => {
                if (!newName.trim() || !newContent.trim()) {
                  toast.error("Name and content required");
                  return;
                }
                upload.mutate({ name: newName, content: newContent });
                setNewName("");
                setNewContent("");
              }}
              disabled={upload.isPending}
              className="rounded-md border border-primary/50 bg-primary/10 px-3 py-1 text-[11.5px] text-primary hover:bg-primary/15 disabled:opacity-50"
            >
              {upload.isPending ? "Saving…" : "Save"}
            </button>
          </div>
        </details>
      </Section>

      <Section title={`Documents (${data?.docs?.length ?? 0})`}>
        {isLoading ? (
          <p className="text-[12px] text-muted-foreground">Loading…</p>
        ) : !data?.docs?.length ? (
          <p className="text-[12px] text-muted-foreground">
            No documents yet — upload above.
          </p>
        ) : (
          <div className="space-y-1">
            {data.docs.map((d) => (
              <div
                key={d.name}
                className="flex items-center gap-2 rounded-md border border-border/40 px-3 py-2 text-[12px]"
              >
                <input
                  type="checkbox"
                  checked={d.enabled}
                  onChange={(e) =>
                    toggle.mutate({ name: d.name, enabled: e.target.checked })
                  }
                  className="shrink-0 cursor-pointer accent-primary"
                  title={d.enabled ? "Disable in retrieval" : "Enable in retrieval"}
                />
                <span
                  className={cn(
                    "flex-1 truncate font-mono",
                    !d.enabled && "text-muted-foreground/70 line-through",
                  )}
                >
                  {d.name}
                </span>
                <span className="shrink-0 text-[11px] text-muted-foreground">
                  {formatBytes(d.bytes)} · {relativeTime(d.updatedAt)}
                </span>
                <button
                  type="button"
                  onClick={() => {
                    if (confirm(`Remove ${d.name}?`)) remove.mutate(d.name);
                  }}
                  className="rounded p-1 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                  aria-label="Remove"
                >
                  <X className="size-3.5" />
                </button>
              </div>
            ))}
          </div>
        )}
      </Section>
    </>
  );
}

// ── Skills ────────────────────────────────────────────────────────────

type Skill = {
  name: string;
  description: string;
  kind: "prompt" | "shell";
  body: string;
  bytes: number;
  updatedAt: number;
};

function SkillsSection({ workspaceId }: { workspaceId: string }) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState<Skill | null>(null);
  const [creating, setCreating] = useState(false);

  const { data, isLoading } = useQuery({
    queryKey: ["ws", workspaceId, "skills"],
    queryFn: async () => {
      const r = await fetch(`/api/workspace/${workspaceId}/skills`);
      return (await r.json()) as { skills: Skill[] };
    },
    refetchOnWindowFocus: false,
  });

  const save = useMutation({
    mutationFn: async (s: Pick<Skill, "name" | "description" | "kind" | "body">) => {
      const r = await fetch(`/api/workspace/${workspaceId}/skills`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(s),
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(j.error ?? r.statusText);
      }
      return r.json();
    },
    onSuccess: () => {
      toast.success("Skill saved");
      setEditing(null);
      setCreating(false);
      qc.invalidateQueries({ queryKey: ["ws", workspaceId, "skills"] });
    },
    onError: (err: Error) => toast.error(`Save failed: ${err.message}`),
  });

  const remove = useMutation({
    mutationFn: async (name: string) => {
      const r = await fetch(
        `/api/workspace/${workspaceId}/skills?name=${encodeURIComponent(name)}`,
        { method: "DELETE" },
      );
      if (!r.ok) throw new Error(r.statusText);
      return r.json();
    },
    onSuccess: () => {
      toast.success("Skill removed");
      qc.invalidateQueries({ queryKey: ["ws", workspaceId, "skills"] });
    },
  });

  if (editing || creating) {
    return (
      <SkillEditor
        initial={editing}
        onCancel={() => {
          setEditing(null);
          setCreating(false);
        }}
        onSave={(s) => save.mutate(s)}
        saving={save.isPending}
      />
    );
  }

  return (
    <Section
      title="Skills"
      icon={<Sparkles className="size-3.5" />}
      hint="Reusable prompt templates + shell macros stored at .jarvis/skills/. V1: store + edit in this UI; V2 wires them to slash commands in the composer."
    >
      <div className="mb-3 flex items-center justify-between">
        <span className="text-[12px] text-muted-foreground">
          {data?.skills?.length ?? 0} skill
          {(data?.skills?.length ?? 0) === 1 ? "" : "s"}
        </span>
        <button
          type="button"
          onClick={() => setCreating(true)}
          className="inline-flex items-center gap-1 rounded-md border border-primary/50 bg-primary/10 px-3 py-1 text-[11.5px] text-primary hover:bg-primary/15"
        >
          <Plus className="size-3.5" />
          New skill
        </button>
      </div>
      {isLoading ? (
        <p className="text-[12px] text-muted-foreground">Loading…</p>
      ) : !data?.skills?.length ? (
        <p className="text-[12px] text-muted-foreground">
          No skills yet — create one.
        </p>
      ) : (
        <div className="space-y-1">
          {data.skills.map((s) => (
            <div
              key={s.name}
              className="flex items-center gap-2 rounded-md border border-border/40 px-3 py-2 text-[12px]"
            >
              <span className="font-mono text-foreground">/{s.name}</span>
              <span className="rounded bg-muted px-1.5 py-0 text-[10px] uppercase tracking-wider text-muted-foreground">
                {s.kind}
              </span>
              <span className="flex-1 truncate text-muted-foreground">
                {s.description || "(no description)"}
              </span>
              <button
                type="button"
                onClick={() => setEditing(s)}
                className="rounded border border-border/60 px-2 py-0.5 text-[11px] hover:bg-accent"
              >
                Edit
              </button>
              <button
                type="button"
                onClick={() => {
                  if (confirm(`Remove /${s.name}?`)) remove.mutate(s.name);
                }}
                className="rounded p-1 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                aria-label="Remove"
              >
                <X className="size-3.5" />
              </button>
            </div>
          ))}
        </div>
      )}
    </Section>
  );
}

function SkillEditor({
  initial,
  onCancel,
  onSave,
  saving,
}: {
  initial: Skill | null;
  onCancel: () => void;
  onSave: (s: { name: string; description: string; kind: "prompt" | "shell"; body: string }) => void;
  saving: boolean;
}) {
  const [name, setName] = useState(initial?.name ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [kind, setKind] = useState<"prompt" | "shell">(initial?.kind ?? "prompt");
  const [body, setBody] = useState(initial?.body ?? "");

  return (
    <Section
      title={initial ? `Edit /${initial.name}` : "New skill"}
      icon={<Sparkles className="size-3.5" />}
    >
      <div className="space-y-3">
        <div>
          <label className="block text-[11px] uppercase tracking-wider text-muted-foreground mb-1">
            Name
          </label>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="optimize-images"
            disabled={!!initial}
            className="w-full rounded-md border border-border bg-background px-3 py-1.5 font-mono text-[12px] outline-none focus:border-primary disabled:opacity-60"
          />
          <p className="mt-1 text-[11px] text-muted-foreground">
            Lowercase, kebab-case. Used as the slash-command name.
          </p>
        </div>
        <div>
          <label className="block text-[11px] uppercase tracking-wider text-muted-foreground mb-1">
            Description
          </label>
          <input
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Compress all PNG/JPG in public/ via sharp"
            className="w-full rounded-md border border-border bg-background px-3 py-1.5 text-[12px] outline-none focus:border-primary"
          />
        </div>
        <div>
          <label className="block text-[11px] uppercase tracking-wider text-muted-foreground mb-1">
            Kind
          </label>
          <div className="flex gap-2">
            {(["prompt", "shell"] as const).map((k) => (
              <button
                key={k}
                type="button"
                onClick={() => setKind(k)}
                className={cn(
                  "rounded-md border px-3 py-1.5 text-[11.5px]",
                  kind === k
                    ? "border-primary/60 bg-primary/15 text-primary"
                    : "border-border/60 hover:bg-accent",
                )}
              >
                {k}
              </button>
            ))}
          </div>
          <p className="mt-1 text-[11px] text-muted-foreground">
            <strong>prompt</strong>: a system-prompt template the model uses
            when invoked. <strong>shell</strong>: a literal command run in the
            sandbox.
          </p>
        </div>
        <div>
          <label className="block text-[11px] uppercase tracking-wider text-muted-foreground mb-1">
            Body
          </label>
          <textarea
            value={body}
            onChange={(e) => setBody(e.target.value)}
            placeholder={
              kind === "prompt"
                ? "You are a helpful assistant. Take the user's request and..."
                : 'bunx sharp-cli --input "public/**/*.{png,jpg}" --output "public/" --format webp'
            }
            rows={10}
            className="w-full resize-y rounded-md border border-border bg-background px-3 py-2 font-mono text-[11.5px] leading-snug outline-none focus:border-primary"
          />
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => onSave({ name, description, kind, body })}
            disabled={saving}
            className="rounded-md border border-primary/50 bg-primary/10 px-3 py-1.5 text-[11.5px] text-primary hover:bg-primary/15 disabled:opacity-50"
          >
            {saving ? "Saving…" : "Save"}
          </button>
          <button
            type="button"
            onClick={onCancel}
            className="rounded-md border border-border px-3 py-1.5 text-[11.5px] hover:bg-accent"
          >
            Cancel
          </button>
        </div>
      </div>
    </Section>
  );
}

// ── Authentication ────────────────────────────────────────────────────

const AUTH_PROVIDERS = [
  { id: "credentials", label: "Email + Password", needsEnv: [] },
  { id: "magic-link", label: "Magic Link (Email)", needsEnv: ["SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM"] },
  { id: "google", label: "Google OAuth", needsEnv: ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"] },
  { id: "github", label: "GitHub OAuth", needsEnv: ["GITHUB_CLIENT_ID", "GITHUB_CLIENT_SECRET"] },
] as const;

type AuthProvider = "credentials" | "magic-link" | "google" | "github";

function AuthSection({
  ws,
  workspaceId,
  onChanged,
}: {
  ws: WorkspaceMeta | null;
  workspaceId: string;
  onChanged: () => void;
}) {
  const qc = useQueryClient();
  const auth = (ws as WorkspaceMeta & { auth?: {
    providers: AuthProvider[];
    sessionMins: number;
    cookieSecure: boolean;
    cookieSameSite: "lax" | "strict" | "none";
    scaffolded?: boolean;
  } })?.auth ?? {
    providers: [] as AuthProvider[],
    sessionMins: 1440,
    cookieSecure: false,
    cookieSameSite: "lax" as const,
    scaffolded: false,
  };
  const [providers, setProviders] = useState<AuthProvider[]>(auth.providers);
  const [sessionMins, setSessionMins] = useState(auth.sessionMins);
  const [cookieSecure, setCookieSecure] = useState(auth.cookieSecure);
  const [cookieSameSite, setCookieSameSite] = useState(auth.cookieSameSite);

  // Re-sync local state when ws.auth changes from outside.
  useEffect(() => {
    if (!ws) return;
    const a = (ws as WorkspaceMeta & { auth?: typeof auth }).auth;
    if (!a) return;
    setProviders(a.providers);
    setSessionMins(a.sessionMins);
    setCookieSecure(a.cookieSecure);
    setCookieSameSite(a.cookieSameSite);
  }, [ws]);

  const save = useMutation({
    mutationFn: () =>
      patchWorkspace(workspaceId, {
        auth: {
          providers,
          sessionMins,
          cookieSecure,
          cookieSameSite,
        },
      }),
    onSuccess: () => {
      toast.success("Auth config saved");
      onChanged();
    },
    onError: (err: Error) => toast.error(`Save failed: ${err.message}`),
  });

  const scaffold = useMutation({
    mutationFn: async () => {
      const r = await fetch(
        `/api/workspace/${workspaceId}/auth/scaffold`,
        { method: "POST" },
      );
      const j = await r.json();
      if (!r.ok) throw new Error(j.hint ?? j.error ?? r.statusText);
      return j;
    },
    onSuccess: (j: { written: string[]; deps: string[]; hint: string }) => {
      toast.success(`Scaffolded ${j.written.length} files`, {
        description: j.hint,
        duration: 8000,
      });
      qc.invalidateQueries({ queryKey: ["ws", workspaceId, "meta"] });
    },
    onError: (err: Error) => toast.error(`Scaffold failed: ${err.message}`),
  });

  const toggle = (p: AuthProvider) => {
    setProviders((cur) =>
      cur.includes(p) ? cur.filter((x) => x !== p) : [...cur, p],
    );
  };

  const envSet = new Set(Object.keys(ws?.envVars ?? {}));

  return (
    <>
      <Section
        title="Providers"
        icon={<Lock className="size-3.5" />}
        hint="Select which sign-in methods your deployed app should support. Scaffold writes Auth.js (next-auth v5) boilerplate into the workspace."
      >
        <div className="space-y-2">
          {AUTH_PROVIDERS.map((p) => {
            const enabled = providers.includes(p.id);
            const missingEnv = p.needsEnv.filter((e) => !envSet.has(e));
            return (
              <div
                key={p.id}
                className={cn(
                  "rounded-md border px-3 py-2",
                  enabled ? "border-primary/40 bg-primary/5" : "border-border/40",
                )}
              >
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={enabled}
                    onChange={() => toggle(p.id)}
                    className="accent-primary"
                  />
                  <span className="flex-1 text-[12.5px] font-medium">
                    {p.label}
                  </span>
                </label>
                {enabled && p.needsEnv.length > 0 && (
                  <div className="mt-1.5 ml-6 text-[11px]">
                    <span className="text-muted-foreground">
                      Required env vars:
                    </span>
                    <span className="ml-1.5 font-mono">
                      {p.needsEnv.map((e, i) => (
                        <span
                          key={e}
                          className={
                            envSet.has(e)
                              ? "text-emerald-500/85"
                              : "text-amber-500/85"
                          }
                        >
                          {e}
                          {i < p.needsEnv.length - 1 && ", "}
                        </span>
                      ))}
                    </span>
                    {missingEnv.length > 0 && (
                      <p className="mt-0.5 text-amber-500/85">
                        Add the missing vars in Settings → Secrets before
                        scaffolding.
                      </p>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </Section>

      <Section title="Session config">
        <div className="space-y-3">
          <div>
            <label className="block text-[11px] uppercase tracking-wider text-muted-foreground mb-1">
              Session lifetime (minutes)
            </label>
            <input
              type="number"
              min={5}
              max={43200}
              value={sessionMins}
              onChange={(e) => setSessionMins(parseInt(e.target.value, 10) || 1440)}
              className="w-32 rounded-md border border-border bg-background px-2 py-1 text-[12px] outline-none focus:border-primary"
            />
            <p className="mt-1 text-[11px] text-muted-foreground">
              5 to 43200 (30 days). Default 1440 (24h).
            </p>
          </div>
          <div>
            <label className="block text-[11px] uppercase tracking-wider text-muted-foreground mb-1">
              SameSite cookie attribute
            </label>
            <div className="flex gap-2">
              {(["lax", "strict", "none"] as const).map((s) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => setCookieSameSite(s)}
                  className={cn(
                    "rounded-md border px-3 py-1 text-[11.5px]",
                    cookieSameSite === s
                      ? "border-primary/60 bg-primary/15 text-primary"
                      : "border-border/60 hover:bg-accent",
                  )}
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={cookieSecure}
              onChange={(e) => setCookieSecure(e.target.checked)}
              className="accent-primary"
            />
            <span className="text-[12px]">
              Cookie <code className="font-mono">Secure</code> flag
            </span>
            <span className="text-[11px] text-muted-foreground">
              (production = on; localhost = off)
            </span>
          </label>
        </div>
      </Section>

      <Section title="Apply">
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => save.mutate()}
            disabled={save.isPending}
            className="rounded-md border border-border px-3 py-1.5 text-[11.5px] hover:bg-accent disabled:opacity-50"
          >
            {save.isPending ? "Saving…" : "Save config"}
          </button>
          <button
            type="button"
            onClick={() => scaffold.mutate()}
            disabled={scaffold.isPending || providers.length === 0}
            className="rounded-md border border-primary/50 bg-primary/10 px-3 py-1.5 text-[11.5px] text-primary hover:bg-primary/15 disabled:opacity-50"
            title={
              providers.length === 0
                ? "Enable at least one provider first"
                : "Write next-auth boilerplate into the workspace"
            }
          >
            {scaffold.isPending
              ? "Scaffolding…"
              : auth.scaffolded
                ? "Re-scaffold"
                : "Scaffold Auth files"}
          </button>
          {auth.scaffolded && (
            <span className="rounded bg-emerald-500/15 px-1.5 py-0.5 text-[10px] uppercase tracking-wider text-emerald-400">
              scaffolded
            </span>
          )}
        </div>
        <p className="mt-2 text-[11.5px] text-muted-foreground">
          Scaffold writes <code className="font-mono">auth.ts</code>,{" "}
          <code className="font-mono">app/api/auth/[...nextauth]/route.ts</code>,{" "}
          <code className="font-mono">middleware.ts</code>, and{" "}
          <code className="font-mono">lib/db/users.ts</code>. After scaffolding,
          run <code className="font-mono">bun install next-auth@beta @auth/core</code>{" "}
          (the AI can do this in chat) and add OAuth env vars in Secrets.
        </p>
      </Section>
    </>
  );
}

// ── User Management (app users in workspace SQLite) ───────────────────

type AppUser = Record<string, unknown> & { id?: string; email?: string };

function UserMgmtSection({ workspaceId }: { workspaceId: string }) {
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

// ── Analytics (parsed from dev.log) ───────────────────────────────────

type AnalyticsResponse = {
  configured: boolean;
  total: number;
  errorCount: number;
  topRoutes: Array<{
    method: string;
    path: string;
    count: number;
    errorRate: number;
    avgMs: number | null;
  }>;
  statusBuckets: { "2xx": number; "3xx": number; "4xx": number; "5xx": number };
  recentErrors: Array<{ method: string; path: string; status: number }>;
  hint?: string;
};

function AnalyticsSection({ workspaceId }: { workspaceId: string }) {
  const { data, isLoading } = useQuery({
    queryKey: ["ws", workspaceId, "analytics"],
    queryFn: async () => {
      const r = await fetch(`/api/workspace/${workspaceId}/analytics`);
      return (await r.json()) as AnalyticsResponse;
    },
    refetchInterval: 8000,
  });

  return (
    <>
      <Section
        title="Request analytics"
        icon={<BarChart3 className="size-3.5" />}
        hint="Parsed from .jarvis/dev.log — captures requests handled by your dev server. Production analytics (page views from real users) requires deploying with edge instrumentation; that's V2."
      >
        {isLoading ? (
          <p className="text-[12px] text-muted-foreground">Loading…</p>
        ) : !data ? (
          <p className="text-[12px] text-muted-foreground">No data.</p>
        ) : !data.configured ? (
          <p className="text-[12px] text-muted-foreground">{data.hint}</p>
        ) : (
          <>
            <div className="grid grid-cols-4 gap-3">
              <Stat label="Requests" value={data.total.toLocaleString()} />
              <Stat
                label="Errors"
                value={data.errorCount.toLocaleString()}
                tone={data.errorCount > 0 ? "warn" : undefined}
              />
              <Stat
                label="Error rate"
                value={
                  data.total === 0
                    ? "—"
                    : `${((data.errorCount / data.total) * 100).toFixed(1)}%`
                }
                tone={
                  data.total > 0 && data.errorCount / data.total > 0.05
                    ? "warn"
                    : undefined
                }
              />
              <Stat
                label="Routes"
                value={data.topRoutes.length.toString()}
              />
            </div>
            <div className="mt-3 grid grid-cols-4 gap-1.5 text-[11px]">
              {(["2xx", "3xx", "4xx", "5xx"] as const).map((b) => (
                <div
                  key={b}
                  className="rounded border border-border/40 px-2 py-1"
                >
                  <div className="text-muted-foreground">{b}</div>
                  <div className="font-mono">{data.statusBuckets[b]}</div>
                </div>
              ))}
            </div>
          </>
        )}
      </Section>

      {data?.configured && data.topRoutes.length > 0 && (
        <Section title="Top routes">
          <div className="space-y-1">
            {data.topRoutes.map((r) => (
              <div
                key={`${r.method} ${r.path}`}
                className="flex items-center gap-3 rounded-md border border-border/40 px-3 py-1.5 text-[12px]"
              >
                <span className="w-12 shrink-0 rounded bg-muted px-1.5 py-0 text-center text-[10px] uppercase tracking-wider text-muted-foreground">
                  {r.method}
                </span>
                <span className="flex-1 truncate font-mono">{r.path}</span>
                {r.errorRate > 0 && (
                  <span
                    className={cn(
                      "shrink-0 text-[10.5px]",
                      r.errorRate > 0.1
                        ? "text-destructive"
                        : "text-amber-500/85",
                    )}
                  >
                    {(r.errorRate * 100).toFixed(0)}% err
                  </span>
                )}
                {r.avgMs != null && (
                  <span className="shrink-0 text-[11px] text-muted-foreground">
                    ~{r.avgMs}ms
                  </span>
                )}
                <span className="shrink-0 font-mono text-[11px] tabular-nums text-foreground">
                  {r.count.toLocaleString()}
                </span>
              </div>
            ))}
          </div>
        </Section>
      )}

      {data?.configured && data.recentErrors.length > 0 && (
        <Section title={`Recent errors (${data.recentErrors.length})`}>
          <div className="space-y-1">
            {data.recentErrors.map((e, i) => (
              <div
                key={i}
                className="flex items-center gap-2 rounded-md border border-destructive/30 bg-destructive/5 px-3 py-1.5 text-[11.5px]"
              >
                <span className="rounded bg-destructive/15 px-1.5 py-0 font-mono text-[10px] text-destructive">
                  {e.status}
                </span>
                <span className="rounded bg-muted px-1.5 py-0 text-[10px] uppercase tracking-wider text-muted-foreground">
                  {e.method}
                </span>
                <span className="flex-1 truncate font-mono">{e.path}</span>
              </div>
            ))}
          </div>
        </Section>
      )}
    </>
  );
}

function Stat({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "warn";
}) {
  return (
    <div
      className={cn(
        "rounded-md border px-3 py-2",
        tone === "warn"
          ? "border-amber-500/30 bg-amber-500/5"
          : "border-border/40 bg-card/30",
      )}
    >
      <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
        {label}
      </div>
      <div className="mt-0.5 font-mono text-[15px]">{value}</div>
    </div>
  );
}

// ── Stub primitive ────────────────────────────────────────────────────

function StubSection({
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
