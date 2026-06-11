"use client";

import { useState, useEffect, useCallback } from "react";
import { Asterisk, ChevronRight, Lightbulb, X } from "lucide-react";
import { CodeSidebar } from "@/components/code/code-sidebar";
import { CodeComposer } from "@/components/code/code-composer";
import { CodeSession } from "@/components/code/code-session";

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
  const [machines, setMachines] = useState<Machine[] | null>(null);
  const [selected, setSelected] = useState<Machine | null>(null);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [bannerOpen, setBannerOpen] = useState(true);

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
  }, [loadMachines, loadSessions]);

  const dispatch = async () => {
    setError(null);
    if (!input.trim()) return;
    if (!selected) {
      setError("Connect a machine first — click the “Default” pill, then run /remote-control on your machine.");
      return;
    }
    setBusy(true);
    try {
      const r = await fetch("/api/bridge/v1/tasks", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ environment_id: selected.environment_id, prompt: input.trim() }),
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
        onSelectSession={(id) => setSessionId(id)}
        onNewSession={() => { setSessionId(null); setInput(""); }}
      />

      <main className="flex flex-1 flex-col overflow-hidden">
          {sessionId ? (
            <CodeSession
              sessionId={sessionId}
              repo={repoLabel(selected)}
              title={sessions.find((s) => s.session_id === sessionId)?.title ?? "New session"}
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
            {bannerOpen && (
              <div className="mb-2 flex items-center gap-2 rounded-xl border border-border/50 bg-card px-3.5 py-2 text-[12.5px]">
                <Lightbulb className="size-3.5 shrink-0 text-amber-500" />
                <span className="flex-1 text-foreground/75">
                  <span className="font-medium text-foreground">Meet Fable 5,</span> built for long-running, complex work.
                  Switch anytime with <span className="text-blue-400">/model</span>. Included in your plan limits until Jun 22.
                </span>
                <button type="button" className="shrink-0 text-[12.5px] text-blue-400 hover:underline">Try it</button>
                <button type="button" aria-label="Dismiss" onClick={() => setBannerOpen(false)} className="shrink-0 text-muted-foreground hover:text-foreground">
                  <X className="size-3.5" />
                </button>
              </div>
            )}
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
            />
          </div>
        </main>
    </div>
  );
}
