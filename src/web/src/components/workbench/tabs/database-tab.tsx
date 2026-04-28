"use client";

// Database panel — visual structure inspired by bolt's "Bolt Database"
// admin (Project Settings sidebar + Tables/Logs/Security Audit/Advanced
// tabs). Most surfaces are empty until a real database is wired into
// the sandbox; the UI itself is here so the integration can land
// progressively.

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Settings as SettingsIcon,
  Globe,
  BarChart3,
  Database as DatabaseIcon,
  ShieldCheck,
  Wrench,
  KeyRound,
  Users,
  HardDrive,
  BookOpen,
  Sparkles,
  History,
  Plug,
  Loader2,
} from "lucide-react";
import { cn } from "@/lib/utils";

type Props = {
  workspaceId: string;
};

type DbTab = "tables" | "logs" | "audit" | "advanced";

type Status = {
  installed: boolean;
  running: boolean;
  databases: string[];
};

async function fetchDbStatus(workspaceId: string): Promise<Status> {
  // Probe the sandbox for postgres/sqlite. Cheap, no external network.
  const cmd = [
    "command -v psql >/dev/null 2>&1 && echo psql=yes || echo psql=no",
    "command -v sqlite3 >/dev/null 2>&1 && echo sqlite3=yes || echo sqlite3=no",
    "(pg_isready -q 2>/dev/null && echo pg=running) || echo pg=stopped",
    "ls *.db *.sqlite *.sqlite3 2>/dev/null | head -10 || true",
  ].join("; ");
  const r = await fetch(`/api/workspace/${workspaceId}/exec`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ command: cmd }),
  });
  const j = await r.json();
  const out = String(j?.stdout ?? "");
  const installed = /psql=yes|sqlite3=yes/.test(out);
  const running = /pg=running/.test(out);
  const sqliteFiles = out
    .split("\n")
    .filter((l) => /\.(db|sqlite3?)$/.test(l.trim()));
  return { installed, running, databases: sqliteFiles };
}

const PROJECT_NAV = [
  { id: "general", label: "General", icon: SettingsIcon },
  { id: "domains", label: "Domains & Hosting", icon: Globe },
  { id: "analytics", label: "Analytics", icon: BarChart3 },
  { id: "database", label: "Database", icon: DatabaseIcon, active: true },
  { id: "auth", label: "Authentication", icon: ShieldCheck },
  { id: "functions", label: "Server Functions", icon: Wrench },
  { id: "secrets", label: "Secrets", icon: KeyRound },
  { id: "users", label: "User Management", icon: Users },
  { id: "storage", label: "File Storage", icon: HardDrive },
  { id: "knowledge", label: "Knowledge", icon: BookOpen },
  { id: "skills", label: "Skills", icon: Sparkles },
  { id: "backups", label: "Backups", icon: History },
] as const;

const PERSONAL_NAV = [
  { id: "p-general", label: "General", icon: SettingsIcon },
  { id: "p-apps", label: "Applications", icon: Plug },
  { id: "p-knowledge", label: "Knowledge", icon: BookOpen },
  { id: "p-mcp", label: "Connectors (MCP)", icon: Plug },
  { id: "p-skills", label: "Skills", icon: Sparkles },
] as const;

export function DatabaseTab({ workspaceId }: Props) {
  const [tab, setTab] = useState<DbTab>("tables");
  const { data: status, isLoading } = useQuery({
    queryKey: ["ws", workspaceId, "db-status"],
    queryFn: () => fetchDbStatus(workspaceId),
    refetchInterval: 8000,
  });

  return (
    <div className="flex h-full">
      {/* Left rail: Project Settings */}
      <aside className="w-56 shrink-0 border-r border-border/50 overflow-y-auto px-3 py-4">
        <NavGroup label="Project Settings">
          {PROJECT_NAV.map((item) => (
            <NavItem
              key={item.id}
              label={item.label}
              icon={<item.icon className="size-3.5" />}
              active={"active" in item && item.active === true}
            />
          ))}
        </NavGroup>
        <div className="mt-5">
          <NavGroup label="Personal Settings">
            {PERSONAL_NAV.map((item) => (
              <NavItem
                key={item.id}
                label={item.label}
                icon={<item.icon className="size-3.5" />}
              />
            ))}
          </NavGroup>
        </div>
      </aside>

      {/* Right: Tables / Logs / Security Audit / Advanced */}
      <div className="flex-1 min-w-0 overflow-y-auto">
        <header className="flex items-center gap-3 px-6 pt-4 pb-2 border-b border-border/40">
          <div className="flex items-center gap-2">
            <DatabaseIcon className="size-4 text-primary" />
            <h2 className="text-[14px] font-semibold">Workspace Database</h2>
          </div>
          <div className="ml-auto flex items-center gap-1 text-[11px] text-muted-foreground">
            {isLoading ? (
              <Loader2 className="size-3 animate-spin" />
            ) : status?.running ? (
              <span className="rounded bg-emerald-500/15 text-emerald-400 px-1.5 py-0.5 uppercase tracking-wider">
                running
              </span>
            ) : status?.installed ? (
              <span className="rounded bg-amber-500/15 text-amber-400 px-1.5 py-0.5 uppercase tracking-wider">
                installed
              </span>
            ) : (
              <span className="rounded bg-muted px-1.5 py-0.5 uppercase tracking-wider">
                not connected
              </span>
            )}
          </div>
        </header>

        <div className="flex items-center gap-4 px-6 py-2 border-b border-border/40">
          <DbTabButton active={tab === "tables"} onClick={() => setTab("tables")}>
            Tables
          </DbTabButton>
          <DbTabButton active={tab === "logs"} onClick={() => setTab("logs")}>
            Logs
          </DbTabButton>
          <DbTabButton active={tab === "audit"} onClick={() => setTab("audit")}>
            Security Audit
          </DbTabButton>
          <DbTabButton active={tab === "advanced"} onClick={() => setTab("advanced")}>
            Advanced
          </DbTabButton>
        </div>

        <div className="px-6 py-6">
          {tab === "tables" && <TablesView status={status} />}
          {tab === "logs" && <Empty title="Logs" desc="Database query logs will appear here once a database is running." />}
          {tab === "audit" && <Empty title="Security Audit" desc="Row-level-security policy audit will appear here once tables exist." />}
          {tab === "advanced" && <Empty title="Advanced" desc="Schema management, extensions, and connection strings will appear here." />}
        </div>
      </div>
    </div>
  );
}

function NavGroup({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="px-2.5 pb-1 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
        {label}
      </div>
      <div className="space-y-px">{children}</div>
    </div>
  );
}

function NavItem({
  label,
  icon,
  active,
}: {
  label: string;
  icon: React.ReactNode;
  active?: boolean;
}) {
  return (
    <div
      className={cn(
        "flex items-center gap-2 rounded-md px-2.5 py-1.5 text-[12.5px] cursor-default",
        active
          ? "bg-accent text-foreground"
          : "text-muted-foreground hover:bg-accent/40 hover:text-foreground",
      )}
    >
      <span className="text-muted-foreground">{icon}</span>
      <span className="truncate">{label}</span>
    </div>
  );
}

function DbTabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "text-[13px] py-1 -mb-[1px] border-b-2 transition-colors",
        active
          ? "text-primary border-primary"
          : "text-muted-foreground border-transparent hover:text-foreground",
      )}
    >
      {children}
    </button>
  );
}

function TablesView({ status }: { status?: Status }) {
  if (!status) {
    return <Loader2 className="size-4 animate-spin text-muted-foreground" />;
  }
  if (!status.installed) {
    return (
      <SetupHint>
        <p className="text-sm">No database tooling found in this sandbox.</p>
        <p className="mt-1 text-xs text-muted-foreground">
          Install Postgres or SQLite from the terminal:
        </p>
        <pre className="mt-3 rounded-md bg-muted/40 px-3 py-2 text-[11px] font-mono text-muted-foreground overflow-x-auto">
{`# Postgres (heavier; full RLS, server-side functions)
sudo apt-get update && sudo apt-get install -y postgresql
service postgresql start

# Or SQLite (lightweight; just a file)
sudo apt-get install -y sqlite3
sqlite3 app.db`}
        </pre>
      </SetupHint>
    );
  }
  if (status.databases.length === 0 && !status.running) {
    return (
      <SetupHint>
        <p className="text-sm">Tooling installed, no databases yet.</p>
        <p className="mt-1 text-xs text-muted-foreground">
          Create one from the terminal — the next refresh will pick it up.
        </p>
      </SetupHint>
    );
  }
  return (
    <div className="space-y-2">
      <div className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
        Detected
      </div>
      {status.databases.map((f) => (
        <div
          key={f}
          className="flex items-center gap-2 rounded-md border border-border/40 px-3 py-2 font-mono text-[12px]"
        >
          <DatabaseIcon className="size-3.5 text-muted-foreground" />
          {f}
          <span className="ml-auto text-[10px] text-muted-foreground">sqlite</span>
        </div>
      ))}
      {status.running && (
        <div className="flex items-center gap-2 rounded-md border border-border/40 px-3 py-2 font-mono text-[12px]">
          <DatabaseIcon className="size-3.5 text-muted-foreground" />
          postgres @ localhost
          <span className="ml-auto text-[10px] text-emerald-400">running</span>
        </div>
      )}
      <p className="text-[11px] text-muted-foreground pt-2">
        Schema browsing, row editing, and policy management are next on the roadmap.
        For now, run <code className="font-mono">psql</code> or{" "}
        <code className="font-mono">sqlite3</code> from the Code tab terminal.
      </p>
    </div>
  );
}

function SetupHint({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-border/50 bg-card/30 p-4">
      {children}
    </div>
  );
}

function Empty({ title, desc }: { title: string; desc: string }) {
  return (
    <div className="rounded-lg border border-border/50 bg-card/30 p-6 text-center">
      <p className="text-sm font-medium">{title}</p>
      <p className="mt-1 text-xs text-muted-foreground max-w-md mx-auto">{desc}</p>
    </div>
  );
}
