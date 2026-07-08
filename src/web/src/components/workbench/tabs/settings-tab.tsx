"use client";

// Thin shell for the workbench Project Settings tab. Each sidebar
// section lives in its own file under ../settings/ (pure move of the
// former 3k-line single file — no behavior change). Shared types,
// fetchers, and UI primitives live in ../settings/shared.tsx.

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Database as DatabaseIcon,
  AlertTriangle,
  Settings as SettingsIcon,
  Globe,
  BarChart3,
  Lock,
  Server,
  Users,
  HardDrive,
  BookOpen,
  Wrench,
  Archive,
  KeyRound,
  type LucideIcon,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { cn } from "@/lib/utils";
import {
  fetchRuntime,
  fetchWorkspace,
  fetchGitStatus,
  fetchDbInfo,
} from "../settings/shared";
import { Header } from "../settings/header";
import { CustomInstructionsSection } from "../settings/custom-instructions";
import { DevCommandSection } from "../settings/dev-command";
import { RuntimeSection } from "../settings/runtime";
import { EnvVarsSection } from "../settings/env-vars";
import { DatabaseSection } from "../settings/database";
import { BackupsSection } from "../settings/backups";
import { ServerFunctionsSection } from "../settings/server-functions";
import { FileStorageSection } from "../settings/file-storage";
import { DangerSection } from "../settings/danger";
import { DomainsHostingSection } from "../settings/domains-hosting";
import { AnalyticsSection } from "../settings/analytics";
import { AuthSection } from "../settings/auth";
import { UserMgmtSection } from "../settings/user-management";
import { KnowledgeSection } from "../settings/knowledge";
import { SkillsSection } from "../settings/skills";

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
  { id: "skills", label: "Skills", icon: Wrench, working: true },
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
