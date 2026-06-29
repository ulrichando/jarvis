"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Zap, Play, Pause, Trash2, Clock, Webhook, GitBranch, Loader2, Check,
  ChevronRight, ArrowUp, Plug, ShieldCheck, Wrench, History,
} from "lucide-react";
import { MODELS_META, PROVIDER_LABEL, type Provider } from "@/lib/ai/models-meta";
import { cronRunsOnDay, parseNaturalSchedule } from "@/lib/cron";

type Trigger =
  | { type: "schedule"; cron: string; label?: string; at?: number }
  | { type: "api"; token: string }
  | { type: "github"; events: string[] };

type Routine = {
  routine_id: string;
  name: string;
  instructions: string;
  repo: string | null;
  model: string | null;
  permission_mode: string | null;
  trigger: Trigger;
  paused: boolean;
  created_at: number;
  last_run_at: number | null;
};

const MODEL_ORDER: Provider[] = ["anthropic", "deepseek", "google", "kimi", "openai"];

const TEMPLATES = [
  "Summarize my open PRs every weekday morning",
  "Triage new issues and flag duplicates each morning",
  "Draft release notes whenever a PR merges",
];

// Cadence → cron + human label. `time`/`dow` apply where relevant.
type Cadence = "once" | "hourly" | "daily" | "weekdays" | "weekly" | "custom" | "natural";
const DOW = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

function buildScheduleTrigger(
  c: Cadence,
  hour: number,
  dow: number,
  custom: string,
  onceAt: string,
  naturalText: string,
): Trigger | null {
  if (c === "natural") {
    const p = parseNaturalSchedule(naturalText);
    return p ? { type: "schedule", cron: p.cron, label: p.label, ...(p.at ? { at: p.at } : {}) } : null;
  }
  switch (c) {
    case "hourly":
      return { type: "schedule", cron: "0 * * * *", label: "Hourly" };
    case "daily":
      return { type: "schedule", cron: `0 ${hour} * * *`, label: `Daily at ${hh(hour)}` };
    case "weekdays":
      return { type: "schedule", cron: `0 ${hour} * * 1-5`, label: `Weekdays at ${hh(hour)}` };
    case "weekly":
      return { type: "schedule", cron: `0 ${hour} * * ${dow}`, label: `Weekly on ${DOW[dow]} at ${hh(hour)}` };
    case "once": {
      // A true one-time run at the chosen local datetime (sets `at`).
      const when = onceAt ? new Date(onceAt) : null;
      if (!when || Number.isNaN(when.getTime())) return null;
      return {
        type: "schedule",
        cron: `${when.getMinutes()} ${when.getHours()} ${when.getDate()} ${when.getMonth() + 1} *`,
        label: `Once on ${when.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}`,
        at: when.getTime(),
      };
    }
    case "custom":
      return custom.trim() ? { type: "schedule", cron: custom.trim(), label: `cron: ${custom.trim()}` } : null;
  }
}
const hh = (h: number) => `${String(h % 12 || 12)}:00 ${h < 12 ? "AM" : "PM"}`;

function triggerLabel(t: Trigger): string {
  if (t.type === "schedule") return t.label || `cron: ${t.cron}`;
  if (t.type === "github") return `GitHub: ${t.events.join(", ")}`;
  return "API / webhook";
}
function triggerBadge(t: Trigger): string {
  if (t.type === "schedule") return t.label?.startsWith("Once") ? "One-time" : (t.label?.split(" ")[0] ?? "Schedule");
  if (t.type === "github") return "GitHub";
  return "API";
}

export function RoutinesView({ onOpenSession }: { onOpenSession: (id: string) => void }) {
  const [routines, setRoutines] = useState<Routine[] | null>(null);
  const [tab, setTab] = useState<"all" | "calendar">("all");
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [createdWebhook, setCreatedWebhook] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);

  const load = useCallback(async () => {
    try {
      const r = await fetch("/api/bridge/v1/routines");
      setRoutines(r.ok ? ((await r.json()) as { routines: Routine[] }).routines : []);
    } catch {
      setRoutines([]);
    }
  }, []);
  useEffect(() => {
    load();
  }, [load]);

  // Open the routine's most recent run (its sessions also list in the sidebar).
  const openLatestRun = async (id: string) => {
    try {
      const r = await fetch(`/api/bridge/v1/routines/${id}/runs`);
      if (!r.ok) return;
      const first = ((await r.json()) as { runs?: { session_id: string }[] }).runs?.[0];
      if (first) onOpenSession(first.session_id);
    } catch {
      /* ignore */
    }
  };

  const runNow = async (id: string) => {
    setBusyId(id);
    try {
      const r = await fetch(`/api/bridge/v1/routines/${id}/run`, { method: "POST" });
      if (r.ok) { const j = (await r.json()) as { session_id: string }; load(); onOpenSession(j.session_id); }
      else { const j = (await r.json().catch(() => ({}))) as { error?: { message?: string } }; setError(j.error?.message ?? "Run failed"); }
    } finally { setBusyId(null); }
  };
  const togglePause = async (r: Routine) => {
    setBusyId(r.routine_id);
    try { await fetch(`/api/bridge/v1/routines/${r.routine_id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ paused: !r.paused }) }); load(); }
    finally { setBusyId(null); }
  };
  const remove = async (id: string) => {
    if (!window.confirm("Delete this routine?")) return;
    setBusyId(id);
    try { await fetch(`/api/bridge/v1/routines/${id}`, { method: "DELETE" }); load(); }
    finally { setBusyId(null); }
  };

  if (showForm) {
    return (
      <NewRoutineForm
        onCancel={() => setShowForm(false)}
        onCreated={(webhook) => { setShowForm(false); setCreatedWebhook(webhook); load(); }}
      />
    );
  }

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto max-w-3xl px-8 py-8">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 text-[20px] font-semibold text-foreground">
            <Zap className="size-5 text-orange-500" /> Routines
          </div>
          <button type="button" onClick={() => setShowForm(true)} className="rounded-lg border border-border/60 px-3 py-1.5 text-[13px] font-medium text-foreground/90 hover:bg-accent/40">+ New routine</button>
        </div>
        <p className="mt-1 text-[13px] text-muted-foreground">Create templated routines that can be kicked off on schedule, by API, or webhook.</p>

        {/* "What do you want automated?" prompt → opens the form */}
        <button
          type="button"
          onClick={() => setShowForm(true)}
          className="mt-4 flex w-full items-center gap-2 rounded-xl border border-border/60 bg-accent/15 px-4 py-3 text-left text-[13px] text-muted-foreground/70 hover:bg-accent/25"
        >
          What do you want automated?
        </button>
        <div className="mt-2 flex flex-wrap gap-2">
          {TEMPLATES.map((t) => (
            <button key={t} type="button" onClick={() => setShowForm(true)} className="rounded-lg border border-border/50 bg-card/40 px-2.5 py-1 text-[12px] text-foreground/70 hover:bg-accent/40">{t}</button>
          ))}
        </div>

        {error && <div className="mt-3 rounded-lg bg-red-500/10 px-3 py-2 text-[12px] text-red-500">{error}</div>}
        {createdWebhook && (
          <div className="mt-3 rounded-lg border border-border/60 bg-accent/15 px-3 py-2 text-[12px] text-foreground/80">
            Webhook URL — POST <code className="text-[11px]">{`{ "token": "<from create>" }`}</code> to run it:
            <div className="mt-1 break-all font-mono text-[11px] text-muted-foreground">{createdWebhook}</div>
          </div>
        )}

        {/* tabs */}
        <div className="mt-5 flex items-center gap-1 border-b border-border/40">
          {(["all", "calendar"] as const).map((t) => (
            <button key={t} type="button" onClick={() => setTab(t)} className={`rounded-t-md px-3 py-1.5 text-[13px] capitalize ${tab === t ? "border-b-2 border-primary text-foreground" : "text-muted-foreground hover:text-foreground"}`}>{t}</button>
          ))}
        </div>

        {routines === null ? (
          <div className="flex items-center gap-2 py-6 text-[13px] text-muted-foreground"><Loader2 className="size-4 animate-spin" /> Loading…</div>
        ) : tab === "all" ? (
          <div className="mt-3 space-y-2">
            {routines.length === 0 ? (
              <div className="rounded-lg bg-accent/15 px-3.5 py-3 text-[13px] text-muted-foreground">No routines yet — describe one above.</div>
            ) : (
              routines.map((r) => (
                <div key={r.routine_id} className="group flex items-center gap-3 rounded-xl border border-border/50 bg-card/40 px-3.5 py-2.5">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="truncate text-[13px] font-medium text-foreground">{r.name}</span>
                      <span className="shrink-0 rounded border border-border/60 px-1.5 py-0.5 text-[10px] text-muted-foreground">{triggerBadge(r.trigger)}</span>
                      {r.paused && <span className="shrink-0 rounded bg-accent px-1.5 py-0.5 text-[10px] text-muted-foreground">Paused</span>}
                    </div>
                    <div className="mt-0.5 truncate text-[11.5px] text-muted-foreground/70">
                      {triggerLabel(r.trigger)}{r.repo ? ` · ${r.repo.split("/").pop()}` : ""}{r.last_run_at ? ` · last run ${new Date(r.last_run_at).toLocaleDateString()}` : ""}
                    </div>
                  </div>
                  <div className="flex items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100">
                    <IconBtn title={r.repo ? "Run now" : "Add a repo to run"} disabled={busyId === r.routine_id || !r.repo} onClick={() => runNow(r.routine_id)}>
                      {busyId === r.routine_id ? <Loader2 className="size-3.5 animate-spin" /> : <Play className="size-3.5" />}
                    </IconBtn>
                    {r.last_run_at && (
                      <IconBtn title="Open latest run" onClick={() => openLatestRun(r.routine_id)}>
                        <History className="size-3.5" />
                      </IconBtn>
                    )}
                    <IconBtn title={r.paused ? "Resume" : "Pause"} disabled={busyId === r.routine_id} onClick={() => togglePause(r)}>
                      {r.paused ? <Check className="size-3.5" /> : <Pause className="size-3.5" />}
                    </IconBtn>
                    <IconBtn title="Delete" danger disabled={busyId === r.routine_id} onClick={() => remove(r.routine_id)}><Trash2 className="size-3.5" /></IconBtn>
                  </div>
                </div>
              ))
            )}
          </div>
        ) : (
          <CalendarView routines={routines} />
        )}
      </div>
    </div>
  );
}

function IconBtn({ children, title, onClick, disabled, danger }: { children: React.ReactNode; title: string; onClick: () => void; disabled?: boolean; danger?: boolean }) {
  return (
    <button type="button" title={title} disabled={disabled} onClick={onClick} className={`flex size-7 items-center justify-center rounded-md disabled:opacity-40 ${danger ? "text-red-500 hover:bg-red-500/10" : "text-muted-foreground hover:bg-accent/50 hover:text-foreground"}`}>{children}</button>
  );
}

// Week strip + upcoming days. Scheduled routines are placed by parsing their
// real cron expression (cronRunsOnDay); one-time routines land on their `at` day.
function CalendarView({ routines }: { routines: Routine[] }) {
  const days = Array.from({ length: 7 }, (_, i) => {
    const d = new Date();
    d.setDate(d.getDate() + i);
    return d;
  });
  const sameDay = (a: Date, b: Date) =>
    a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
  const runsOn = (r: Routine, d: Date): boolean => {
    if (r.paused || r.trigger.type !== "schedule") return false;
    if (typeof r.trigger.at === "number") return sameDay(new Date(r.trigger.at), d);
    return cronRunsOnDay(r.trigger.cron, d);
  };
  const labelFor = (i: number, d: Date) =>
    i === 0 ? "Today" : i === 1 ? "Tomorrow" : d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
  return (
    <div className="mt-4">
      <div className="mb-3 grid grid-cols-7 gap-1">
        {days.map((d, i) => (
          <div key={i} className={`rounded-lg px-2 py-2 text-center ${i === 0 ? "bg-accent/40" : "bg-card/30"}`}>
            <div className="text-[10px] uppercase text-muted-foreground/60">{d.toLocaleDateString(undefined, { weekday: "short" })}</div>
            <div className="text-[15px] font-medium text-foreground">{d.getDate()}</div>
          </div>
        ))}
      </div>
      <div className="space-y-3">
        {days.map((d, i) => {
          const todays = routines.filter((r) => runsOn(r, d));
          return (
            <div key={i}>
              <div className="text-[13px] font-medium text-foreground">{labelFor(i, d)}</div>
              {todays.length === 0 ? (
                <div className="mt-1 text-[12px] text-muted-foreground/60">No routines scheduled</div>
              ) : (
                <div className="mt-1 space-y-1">
                  {todays.map((r) => (
                    <div key={r.routine_id} className="flex items-center gap-2 rounded-lg bg-accent/15 px-3 py-1.5 text-[12.5px] text-foreground/80">
                      <Clock className="size-3 text-muted-foreground" /> {r.name}
                      <span className="text-muted-foreground/60">· {triggerLabel(r.trigger)}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── New routine form (matches the reference: trigger cards + tabs) ──────────
function NewRoutineForm({ onCancel, onCreated }: { onCancel: () => void; onCreated: (webhook: string | null) => void }) {
  const [name, setName] = useState("");
  const [instructions, setInstructions] = useState("");
  const [repo, setRepo] = useState("");
  const [repos, setRepos] = useState<string[]>([]);
  const [model, setModel] = useState("claude-sonnet-4-6");
  const [trigKind, setTrigKind] = useState<"schedule" | "github" | "api">("schedule");
  const [cadence, setCadence] = useState<Cadence>("daily");
  const [hour, setHour] = useState(9);
  const [dow, setDow] = useState(1);
  const [custom, setCustom] = useState("");
  const [onceAt, setOnceAt] = useState("");
  const [natural, setNatural] = useState("");
  const [ghEvents, setGhEvents] = useState("pull_request");
  // GitHub-event filters (claude.ai/code): only fire when these match.
  const [ghFilters, setGhFilters] = useState<{
    author?: string;
    baseBranch?: string;
    titleContains?: string;
    labels?: string;
  }>({});
  const [tab, setTab] = useState<"connectors" | "behavior" | "permissions">("connectors");
  const [autofix, setAutofix] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/github/repos").then((r) => (r.ok ? r.json() : null)).then((d: { ok?: boolean; repos?: { full_name: string }[] } | null) => { if (d?.ok && d.repos) setRepos(d.repos.map((x) => x.full_name)); }).catch(() => {});
  }, []);

  const create = async () => {
    if (!name.trim() || !instructions.trim()) { setError("Name and instructions are required."); return; }
    let trigger: Trigger | { type: string; events?: string[] } | null;
    if (trigKind === "schedule") trigger = buildScheduleTrigger(cadence, hour, dow, custom, onceAt, natural);
    else if (trigKind === "github") {
      const filters: Record<string, unknown> = {};
      if (ghFilters.author?.trim()) filters.author = ghFilters.author.trim();
      if (ghFilters.baseBranch?.trim()) filters.baseBranch = ghFilters.baseBranch.trim();
      if (ghFilters.titleContains?.trim()) filters.titleContains = ghFilters.titleContains.trim();
      const labels = (ghFilters.labels ?? "").split(",").map((s) => s.trim()).filter(Boolean);
      if (labels.length) filters.labels = labels;
      trigger = {
        type: "github",
        events: ghEvents.split(",").map((s) => s.trim()).filter(Boolean),
        ...(Object.keys(filters).length ? { filters } : {}),
      } as { type: string; events?: string[] };
    } else trigger = { type: "api" };
    if (!trigger) {
      setError(
        cadence === "natural"
          ? "Couldn't parse that schedule — try e.g. \"every day at 9am\" or \"in 2 hours\"."
          : cadence === "once"
          ? "Pick a date and time for the one-time run."
          : "Enter a cron expression for the custom schedule.",
      );
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const r = await fetch("/api/bridge/v1/routines", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name.trim(), instructions: instructions.trim(), repo: repo || null, model, trigger }),
      });
      if (!r.ok) { const j = (await r.json().catch(() => ({}))) as { error?: { message?: string } }; setError(j.error?.message ?? `Create failed (${r.status})`); return; }
      const j = (await r.json()) as { routine_id: string; api_token?: string };
      onCreated(j.api_token ? `${window.location.origin}/api/bridge/v1/routines/${j.routine_id}/run` : null);
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  };

  const input = "w-full rounded-lg border border-border/60 bg-accent/20 px-3 py-2 text-[13px] text-foreground placeholder:text-muted-foreground/50 focus:outline-none focus:ring-1 focus:ring-primary/40";

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto max-w-3xl px-8 py-8">
        <div className="flex items-center gap-1.5 text-[13px] text-muted-foreground">
          <Zap className="size-3.5 text-orange-500" /> Routines <ChevronRight className="size-3.5" /> <span className="text-foreground">New routine</span>
        </div>

        <div className="mt-6 space-y-5">
          <div>
            <label className="mb-1 block text-[12px] text-foreground/70">Name *</label>
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g., Daily code review" className={input} autoFocus />
          </div>
          <div>
            <label className="mb-1 block text-[12px] text-foreground/70">Instructions</label>
            <div className="rounded-lg border border-border/60 bg-accent/20">
              <textarea value={instructions} onChange={(e) => setInstructions(e.target.value)} rows={3} placeholder="Describe what Jarvis should do in each run" className="w-full resize-none bg-transparent px-3 py-2 text-[13px] text-foreground placeholder:text-muted-foreground/50 focus:outline-none" />
              <div className="flex items-center justify-between border-t border-border/40 px-3 py-1.5 text-[11.5px] text-muted-foreground/70">
                <select value={repo} onChange={(e) => setRepo(e.target.value)} className="bg-transparent focus:outline-none">
                  <option value="">Select a repository</option>
                  {repos.map((r) => <option key={r} value={r}>{r}</option>)}
                </select>
                <select value={model} onChange={(e) => setModel(e.target.value)} className="bg-transparent focus:outline-none">
                  {MODEL_ORDER.flatMap((p) => Object.values(MODELS_META).filter((m) => m.provider === p).map((m) => <option key={m.id} value={m.id}>{m.label}</option>))}
                </select>
              </div>
            </div>
          </div>

          {/* trigger cards */}
          <div>
            <label className="mb-2 block text-[12px] text-foreground/70">Select a trigger</label>
            <div className="space-y-2">
              <TriggerCard active={trigKind === "schedule"} onClick={() => setTrigKind("schedule")} icon={Clock} title="Schedule" desc="Run on a recurring cron schedule or once at a future time" />
              {trigKind === "schedule" && (
                <div className="ml-4 rounded-lg border border-border/50 bg-card/40 p-3">
                  <div className="flex flex-wrap gap-1.5">
                    {(["once", "hourly", "daily", "weekdays", "weekly", "custom", "natural"] as Cadence[]).map((c) => (
                      <button key={c} type="button" onClick={() => setCadence(c)} className={`rounded-md px-2.5 py-1 text-[12px] capitalize ${cadence === c ? "bg-primary/15 text-foreground" : "text-muted-foreground hover:bg-accent/40"}`}>{c === "natural" ? "Phrase" : c}</button>
                    ))}
                  </div>
                  <div className="mt-2 flex items-center gap-2">
                    {(cadence === "daily" || cadence === "weekdays" || cadence === "weekly") && (
                      <><span className="text-[12px] text-muted-foreground">At</span>
                      <select value={hour} onChange={(e) => setHour(Number(e.target.value))} className={`${input} w-auto`}>{Array.from({ length: 24 }, (_, h) => <option key={h} value={h}>{hh(h)}</option>)}</select></>
                    )}
                    {cadence === "weekly" && (
                      <select value={dow} onChange={(e) => setDow(Number(e.target.value))} className={`${input} w-auto`}>{DOW.map((d, i) => <option key={d} value={i}>{d}</option>)}</select>
                    )}
                    {cadence === "once" && (
                      <><span className="text-[12px] text-muted-foreground">At</span>
                      <input type="datetime-local" value={onceAt} onChange={(e) => setOnceAt(e.target.value)} className={`${input} w-auto`} /></>
                    )}
                    {cadence === "custom" && (
                      <input value={custom} onChange={(e) => setCustom(e.target.value)} placeholder="*/5 * * * *" className={`${input} font-mono`} />
                    )}
                    {cadence === "natural" && (
                      <input value={natural} onChange={(e) => setNatural(e.target.value)} placeholder='e.g. "every weekday at 9am" or "in 2 hours"' className={input} />
                    )}
                  </div>
                  <p className="mt-1.5 text-[11px] text-muted-foreground/60">Scheduled routines auto-run in the background (checked every ~90s); Run now + API + GitHub triggers also work.</p>
                </div>
              )}
              <TriggerCard active={trigKind === "github"} onClick={() => setTrigKind("github")} icon={GitBranch} title="GitHub event" desc="Run when a GitHub webhook event fires" disabled={!repo} disabledNote="Select a repository first" />
              {trigKind === "github" && repo && (
                <div className="ml-4 space-y-2 rounded-lg border border-border/50 bg-card/40 p-3">
                  <input value={ghEvents} onChange={(e) => setGhEvents(e.target.value)} placeholder="Events: pull_request, release" className={input} />
                  <div className="text-[11px] text-muted-foreground/60">Filters (optional — fire only when matched)</div>
                  <div className="grid grid-cols-2 gap-2">
                    <input value={ghFilters.author ?? ""} onChange={(e) => setGhFilters((f) => ({ ...f, author: e.target.value }))} placeholder="author (login)" className={input} />
                    <input value={ghFilters.baseBranch ?? ""} onChange={(e) => setGhFilters((f) => ({ ...f, baseBranch: e.target.value }))} placeholder="base branch" className={input} />
                    <input value={ghFilters.titleContains ?? ""} onChange={(e) => setGhFilters((f) => ({ ...f, titleContains: e.target.value }))} placeholder="title contains" className={input} />
                    <input value={ghFilters.labels ?? ""} onChange={(e) => setGhFilters((f) => ({ ...f, labels: e.target.value }))} placeholder="labels (comma-sep)" className={input} />
                  </div>
                </div>
              )}
              <TriggerCard active={trigKind === "api"} onClick={() => setTrigKind("api")} icon={Webhook} title="API" desc="Trigger from your own code by sending a POST request" />
            </div>
          </div>

          {/* tabs */}
          <div>
            <div className="flex items-center gap-1 border-b border-border/40">
              {(["connectors", "behavior", "permissions"] as const).map((t) => (
                <button key={t} type="button" onClick={() => setTab(t)} className={`px-3 py-1.5 text-[13px] capitalize ${tab === t ? "border-b-2 border-primary text-foreground" : "text-muted-foreground hover:text-foreground"}`}>{t}</button>
              ))}
            </div>
            <div className="py-3 text-[12.5px] text-muted-foreground">
              {tab === "connectors" && (
                <div className="flex items-center gap-2"><Plug className="size-3.5" /> MCP connectors are managed in Settings → Connectors and available to every run.</div>
              )}
              {tab === "behavior" && (
                <button type="button" onClick={() => setAutofix((a) => !a)} className="flex w-full items-center gap-3 rounded-lg border border-border/50 px-3 py-2 text-left hover:bg-accent/20">
                  <Wrench className="size-4 text-muted-foreground" />
                  <span className="flex-1"><span className="block text-[13px] text-foreground">Auto-fix pull requests</span><span className="text-[11.5px]">Watch CI + review comments on PRs this routine opens, and push fixes.</span></span>
                  <span className={`relative h-5 w-9 shrink-0 rounded-full transition-colors ${autofix ? "bg-primary" : "bg-accent"}`}><span className={`absolute top-0.5 size-4 rounded-full bg-white transition-transform ${autofix ? "translate-x-4" : "translate-x-0.5"}`} /></span>
                </button>
              )}
              {tab === "permissions" && (
                <div className="flex items-center gap-2"><ShieldCheck className="size-3.5" /> {repo ? `Tools run under ${model} with the routine's permission mode.` : "Add a repository to configure permissions."}</div>
              )}
            </div>
          </div>

          {error && <div className="rounded-lg bg-red-500/10 px-3 py-2 text-[12px] text-red-500">{error}</div>}
          <div className="flex justify-end gap-2">
            <button type="button" onClick={onCancel} className="rounded-lg border border-border px-3 py-1.5 text-[13px] text-foreground/80 hover:bg-accent/40">Cancel</button>
            <button type="button" onClick={create} disabled={busy || !name.trim() || !instructions.trim()} className="flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-[13px] font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-40">{busy && <Loader2 className="size-3.5 animate-spin" />} Create</button>
          </div>
        </div>
      </div>
    </div>
  );
}

function TriggerCard({ active, onClick, icon: Icon, title, desc, disabled, disabledNote }: { active: boolean; onClick: () => void; icon: typeof Clock; title: string; desc: string; disabled?: boolean; disabledNote?: string }) {
  return (
    <button type="button" onClick={onClick} disabled={disabled} className={`flex w-full items-center gap-3 rounded-lg border px-3 py-2.5 text-left transition-colors disabled:opacity-50 ${active ? "border-primary/60 bg-primary/10" : "border-border/60 hover:bg-accent/20"}`}>
      <Icon className="size-4 shrink-0 text-muted-foreground" />
      <span className="flex-1">
        <span className="block text-[13px] text-foreground">{title}</span>
        <span className="text-[11.5px] text-muted-foreground">{desc}</span>
      </span>
      {disabled && disabledNote && <span className="text-[11px] text-muted-foreground/60">{disabledNote}</span>}
      {active && !disabled && <ArrowUp className="size-3.5 rotate-90 text-primary" />}
    </button>
  );
}
