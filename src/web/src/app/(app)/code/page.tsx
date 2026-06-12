"use client";

import { useState, useEffect, useCallback } from "react";
import { Asterisk, ChevronRight, X, Shield, ExternalLink, Check } from "lucide-react";
import { CodeSidebar } from "@/components/code/code-sidebar";
import { CodeComposer } from "@/components/code/code-composer";
import { CodeSession } from "@/components/code/code-session";
import { CodePanels, type PanelName } from "@/components/code/code-panels";

type Machine = {
  environment_id: string;
  machine_name: string;
  directory: string;
  branch: string | null;
  git_repo_url: string | null;
  worker_type: string;
  last_seen_at: number;
};

type SessionSummary = {
  session_id: string;
  title: string;
  preview: string;
  repo: string | null;
  machine_name: string | null;
  created_at: number;
  status: "needs_input" | "working" | "done";
  pinned?: boolean;
  read?: boolean;
  group_id?: string | null;
  group_name?: string | null;
};

function repoLabel(m: Machine | null): string | null {
  if (!m) return null;
  if (m.git_repo_url) {
    const s = m.git_repo_url.replace(/\.git$/, "").split("/");
    return s.slice(-2).join("/") || (s.slice(-1)[0] ?? null);
  }
  return m.directory.split("/").filter(Boolean).slice(-1)[0] ?? null;
}

function timeAgo(ts: number): string {
  const s = Math.max(0, Math.floor((Date.now() - ts) / 1000));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h`;
  const d = Math.floor(h / 24);
  if (d < 7) return `${d}d`;
  return `${Math.floor(d / 7)}w`;
}

const STATUS_META: Record<SessionSummary["status"], { dot: string; label: string; text: string }> = {
  needs_input: { dot: "bg-amber-500", label: "Needs input", text: "text-amber-500/90" },
  working: { dot: "bg-blue-500 animate-pulse", label: "Working", text: "text-blue-500/90" },
  done: { dot: "bg-muted-foreground/40", label: "Done", text: "text-muted-foreground" },
};

export default function CodePage() {
  const [input, setInput] = useState("");
  // Permission mode for dispatch + live switching (ExternalPermissionMode).
  const [mode, setMode] = useState("acceptEdits");
  // GitHub repo picked in the composer → tasks run in a cloud container.
  const [cloudRepo, setCloudRepo] = useState<string | null>(null);
  const [machines, setMachines] = useState<Machine[] | null>(null);
  const [selected, setSelected] = useState<Machine | null>(null);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [panels, setPanels] = useState({ diff: false, background: false, plan: false });
  const [shareOpen, setShareOpen] = useState(false);
  const [shareVisibility, setShareVisibility] = useState<"private" | "public">("private");

  const loadMachines = useCallback(async () => {
    try {
      const r = await fetch("/api/bridge/v1/environments");
      if (r.ok) {
        const j = (await r.json()) as { environments: Machine[] };
        setMachines(j.environments);
        setSelected((cur) => cur ?? (j.environments.length === 1 ? j.environments[0] : null));
      } else {
        setMachines([]);
      }
    } catch {
      setMachines([]);
    }
  }, []);

  const loadSessions = useCallback(async () => {
    try {
      const r = await fetch("/api/bridge/v1/sessions");
      if (r.ok) {
        const j = (await r.json()) as { sessions: SessionSummary[] };
        setSessions(j.sessions);
      }
    } catch {
      /* keep prior */
    }
  }, []);

  useEffect(() => {
    loadMachines();
    loadSessions();
    // Deep link from "Copy link" (/code?s=<id>) — open that session on load.
    const s = new URLSearchParams(window.location.search).get("s");
    if (s) setSessionId(s);
  }, [loadMachines, loadSessions]);

  const changeMode = (m: string) => {
    setMode(m);
    // Session open → apply live via a set_permission_mode control_request.
    // Otherwise the choice rides the next task dispatch as permission_mode.
    if (sessionId) {
      fetch(`/api/bridge/v1/sessions/${sessionId}/messages`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode: m }),
      }).catch(() => {});
    }
  };

  const dispatch = async () => {
    setError(null);
    if (!input.trim()) return;
    // Session open → the composer messages THAT session (one composer for
    // both modes; the session view has no input of its own). No session →
    // dispatch a new task to the selected machine.
    if (sessionId) {
      setBusy(true);
      try {
        const r = await fetch(`/api/bridge/v1/sessions/${sessionId}/messages`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text: input.trim() }),
        });
        if (r.ok) {
          setInput("");
        } else {
          const j = (await r.json().catch(() => ({}))) as { error?: { message?: string } };
          setError(j.error?.message ?? `Send failed (${r.status})`);
        }
      } catch (e) {
        setError(String(e));
      } finally {
        setBusy(false);
      }
      return;
    }
    // Explicit repo pick → cloud-container dispatch: get-or-create the repo's
    // container target (idempotent per user+repo) and run the task in it.
    let environmentId = selected?.environment_id ?? null;
    if (cloudRepo) {
      setBusy(true);
      try {
        const r = await fetch("/api/bridge/v1/environments/cloud", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ repo: cloudRepo }),
        });
        if (!r.ok) {
          const j = (await r.json().catch(() => ({}))) as { error?: { message?: string } };
          setError(j.error?.message ?? `Cloud target failed (${r.status})`);
          setBusy(false);
          return;
        }
        environmentId = ((await r.json()) as { environment_id: string }).environment_id;
        loadMachines();
      } catch (e) {
        setError(String(e));
        setBusy(false);
        return;
      }
    }
    if (!environmentId) {
      setError("Pick a repo (cloud container) or connect a machine — run /remote-control on your machine.");
      setBusy(false);
      return;
    }
    if (selected?.worker_type === "claude_code_repl" && !cloudRepo) {
      setError("That machine is an attached REPL session (attach-only) — it can't run new tasks. Pick a repo to use a cloud container instead.");
      return;
    }
    setBusy(true);
    try {
      const r = await fetch("/api/bridge/v1/tasks", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          environment_id: environmentId,
          prompt: input.trim(),
          permission_mode: mode,
        }),
      });
      if (r.ok) {
        const j = (await r.json()) as { session_id: string };
        setSessionId(j.session_id);
        setInput("");
        loadSessions();
      } else {
        const j = (await r.json().catch(() => ({}))) as { error?: string };
        setError(j.error ?? `Dispatch failed (${r.status})`);
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    // Full-screen overlay so /code presents like standalone Claude Code.
    <div className="fixed inset-0 z-40 flex bg-background text-foreground overflow-hidden">
      <CodeSidebar
        sessions={sessions}
        activeSessionId={sessionId}
        onSelectSession={(id) => setSessionId(id || null)}
        onNewSession={() => { setSessionId(null); setInput(""); }}
        onRefresh={loadSessions}
        onShareSession={(id) => { setSessionId(id); setShareOpen(true); }}
      />

      <main className="flex flex-1 overflow-hidden">
        {/* chat column (messages + composer) */}
        <div className="flex min-w-0 flex-1 flex-col">
          {sessionId ? (
            <CodeSession
              sessionId={sessionId}
              repo={repoLabel(selected)}
              title={sessions.find((s) => s.session_id === sessionId)?.title ?? "New session"}
              panels={panels}
              onTogglePanel={(p) => setPanels((s) => ({ ...s, [p]: !s[p] }))}
              onShare={() => setShareOpen(true)}
            />
          ) : (
            <div className="flex-1 overflow-y-auto">
              <div className="mx-auto max-w-3xl px-8 pt-8">
                <div className="flex items-center gap-2.5 text-[24px] font-serif font-semibold text-foreground">
                  <Asterisk className="size-6 text-orange-500" strokeWidth={2.5} />
                  <span>Welcome back, Ulrich</span>
                </div>

                <div className="mt-8">
                  <div className="mb-2 text-[12px] font-medium text-muted-foreground">Sessions</div>
                  <div className="space-y-1">
                    {sessions.length === 0 ? (
                      <div className="rounded-lg bg-accent/20 px-3.5 py-3 text-[13px] text-muted-foreground">
                        No sessions yet — describe a task below to start one.
                      </div>
                    ) : (
                      sessions.map((s) => {
                        const m = STATUS_META[s.status];
                        return (
                          <button
                            key={s.session_id}
                            type="button"
                            onClick={() => setSessionId(s.session_id)}
                            className="group flex w-full items-center gap-2.5 rounded-lg bg-accent/20 px-3.5 py-2.5 text-left hover:bg-accent/40 transition-colors"
                          >
                            <span className={`size-1.5 shrink-0 rounded-full ${m.dot}`} />
                            <span className={`shrink-0 text-[12px] font-medium ${m.text}`}>{m.label}</span>
                            <span className="shrink-0 text-[13px] font-medium text-foreground">{s.title}</span>
                            <span className="min-w-0 flex-1 truncate text-[13px] text-muted-foreground/80">{s.preview}</span>
                            {s.repo && <span className="shrink-0 text-[12px] text-muted-foreground">{s.repo}</span>}
                            <span className="shrink-0 text-[12px] text-muted-foreground">{timeAgo(s.created_at)}</span>
                            <ChevronRight className="size-4 shrink-0 text-muted-foreground/60" />
                          </button>
                        );
                      })
                    )}
                  </div>
                </div>
              </div>
            </div>
          )}

          <div className="mx-auto w-full max-w-3xl px-6 pb-6">
            {error && <div className="mb-2 text-[12px] text-red-500">{error}</div>}
            <CodeComposer
              value={input}
              onChange={setInput}
              onSubmit={dispatch}
              busy={busy}
              machines={machines}
              selected={selected}
              onPickMachine={setSelected}
              onRefreshMachines={loadMachines}
              placeholder={sessionId ? "Type / for commands" : "Describe a task or ask a question"}
              showPills={!sessionId}
              mode={mode}
              onModeChange={changeMode}
              onPickRepo={setCloudRepo}
            />
          </div>
        </div>

        {/* right-side panels (session mode) */}
        {sessionId && (panels.diff || panels.background || panels.plan) && (
          <CodePanels
            panels={panels}
            onClose={(p: PanelName) => setPanels((s) => ({ ...s, [p]: false }))}
            baseBranch={selected?.branch ?? "main"}
            workBranch={`jarvis/${sessionId.slice(0, 8)}`}
          />
        )}
      </main>

      {/* Share session modal */}
      {shareOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={() => setShareOpen(false)}>
          <div className="w-[420px] max-w-[90vw] rounded-2xl border border-border bg-card p-5 shadow-xl" onClick={(e) => e.stopPropagation()}>
            <div className="mb-1 flex items-center justify-between">
              <div className="text-[15px] font-semibold text-foreground">Share session</div>
              <button type="button" onClick={() => setShareOpen(false)} aria-label="Close" className="text-muted-foreground hover:text-foreground">
                <X className="size-4" />
              </button>
            </div>
            <div className="mb-4 text-[12.5px] text-muted-foreground">Showcase your work and how you code with Jarvis.</div>
            {([
              { key: "private", icon: Shield, title: "Private", sub: "Only you have access" },
              { key: "public", icon: ExternalLink, title: "Public", sub: "Anyone with the link can view" },
            ] as const).map((o) => (
              <button
                key={o.key}
                type="button"
                onClick={() => setShareVisibility(o.key)}
                className="flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-left hover:bg-accent/40"
              >
                <o.icon className="size-4 text-muted-foreground" />
                <div className="flex-1">
                  <div className="text-[13px] font-medium text-foreground">{o.title}</div>
                  <div className="text-[12px] text-muted-foreground">{o.sub}</div>
                </div>
                {shareVisibility === o.key && <Check className="size-4 text-foreground" />}
              </button>
            ))}
            <div className="mt-3 text-[11.5px] leading-relaxed text-muted-foreground/70">
              Don&apos;t share personal information or third-party content without permission, and see our{" "}
              <span className="text-blue-400">Usage Policy</span>.
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
