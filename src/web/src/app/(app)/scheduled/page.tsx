"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import {
  AlarmClock,
  ChevronDown,
  ChevronRight,
  Coffee,
  ListChecks,
  Loader2,
  Pause,
  Play,
  Search,
  Trash2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
} from "@/components/ui/dropdown-menu";
import { cronMatches, parseNaturalSchedule } from "@/lib/cron";
import { cn, formatRelativeTime } from "@/lib/utils";

// Scheduled CHAT task — Home functionality only. Runs a prompt as a normal
// conversation on a schedule (results land in chats), fully separate from
// the code shell's repo routines.
type ScheduledChat = {
  id: string;
  name: string;
  prompt: string;
  cron: string;
  label: string;
  at?: number;
  paused: boolean;
  created_at: number;
  last_run_at: number | null;
  last_conversation_id: string | null;
};

// Next matching cron minute within a week; null = nothing scheduled (spent
// one-time task). ponytail: minute-scan (~10k checks worst case) — fine for
// a handful of tasks.
function nextRun(t: ScheduledChat): number | null {
  if (t.at) return t.at > Date.now() && !t.last_run_at ? t.at : null;
  const d = new Date();
  d.setSeconds(0, 0);
  for (let i = 0; i < 7 * 24 * 60; i++) {
    d.setMinutes(d.getMinutes() + 1);
    if (cronMatches(t.cron, d)) return d.getTime();
  }
  return null;
}

function nextRunLabel(ts: number | null): string {
  if (ts === null) return "Done";
  const d = new Date(ts);
  const today = new Date();
  const time = d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  if (d.toDateString() === today.toDateString()) return `Next run today at ${time}`;
  const tomorrow = new Date(today.getTime() + 24 * 60 * 60 * 1000);
  if (d.toDateString() === tomorrow.toDateString()) return `Next run tomorrow at ${time}`;
  return `Next run ${d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" })} at ${time}`;
}

type Sort = "next" | "name" | "last";
const SORT_LABEL: Record<Sort, string> = {
  next: "Next run",
  name: "Name",
  last: "Last run",
};

// Templates launch the setup INTERVIEW in a chat (claude.ai flow): Jarvis
// asks a couple of questions, then creates the task itself. `slug` maps to
// SCHEDULE_TEMPLATES on the chat side.
const TEMPLATES = [
  { icon: Coffee, chip: "Daily brief", slug: "daily-brief" },
  { icon: ListChecks, chip: "Weekly review", slug: "weekly-review" },
];

export default function ScheduledPage() {
  const router = useRouter();
  const [tasks, setTasks] = useState<ScheduledChat[] | null>(null);
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<Sort>("next");
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Inline "New task" form state.
  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState("");
  const [prompt, setPrompt] = useState("");
  const [when, setWhen] = useState("");
  const [saving, setSaving] = useState(false);
  const parsedWhen = useMemo(() => parseNaturalSchedule(when), [when]);

  const load = useCallback(async () => {
    try {
      const r = await fetch("/api/scheduled");
      setTasks(r.ok ? ((await r.json()) as { tasks: ScheduledChat[] }).tasks : []);
    } catch {
      setTasks([]);
    }
  }, []);
  useEffect(() => {
    load();
  }, [load]);

  const rows = useMemo(() => {
    const query = q.trim().toLowerCase();
    const withNext = (tasks ?? [])
      .filter(
        (t) =>
          !query ||
          t.name.toLowerCase().includes(query) ||
          t.prompt.toLowerCase().includes(query),
      )
      .map((t) => ({ t, next: nextRun(t) }));
    withNext.sort((a, b) => {
      if (sort === "name") return a.t.name.localeCompare(b.t.name);
      if (sort === "last") return (b.t.last_run_at ?? 0) - (a.t.last_run_at ?? 0);
      return (a.next ?? Infinity) - (b.next ?? Infinity);
    });
    return withNext;
  }, [tasks, q, sort]);

  const openForm = () => {
    setName("");
    setPrompt("");
    setWhen("");
    setShowForm(true);
  };

  const create = async () => {
    if (!parsedWhen || !name.trim() || !prompt.trim()) return;
    setSaving(true);
    setError(null);
    try {
      const r = await fetch("/api/scheduled", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: name.trim(),
          prompt: prompt.trim(),
          cron: parsedWhen.cron,
          label: parsedWhen.label,
          ...(parsedWhen.at ? { at: parsedWhen.at } : {}),
        }),
      });
      if (r.ok) {
        setShowForm(false);
        await load();
      } else {
        const j = (await r.json().catch(() => ({}))) as { error?: string };
        setError(j.error ?? `Create failed (${r.status})`);
      }
    } finally {
      setSaving(false);
    }
  };

  const runNow = async (id: string) => {
    setBusyId(id);
    setError(null);
    try {
      const r = await fetch(`/api/scheduled/${id}/run`, { method: "POST" });
      const j = (await r.json().catch(() => ({}))) as {
        conversation_id?: string;
        error?: string;
      };
      if (r.ok && j.conversation_id) router.push(`/chat/${j.conversation_id}`);
      else setError(j.error ?? "Run failed");
    } finally {
      setBusyId(null);
    }
  };
  const togglePause = async (t: ScheduledChat) => {
    setBusyId(t.id);
    try {
      await fetch(`/api/scheduled/${t.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ paused: !t.paused }),
      });
      await load();
    } finally {
      setBusyId(null);
    }
  };
  const remove = async (id: string) => {
    if (!window.confirm("Delete this scheduled task?")) return;
    setBusyId(id);
    try {
      await fetch(`/api/scheduled/${id}`, { method: "DELETE" });
      await load();
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-4xl px-8 py-10">
        <div className="flex items-start justify-between gap-4">
          <h1 className="font-serif text-[28px] font-semibold tracking-tight">
            Scheduled tasks
          </h1>
          <div className="flex shrink-0 items-center gap-2">
            <DropdownMenu>
              <DropdownMenuTrigger
                render={
                  <button className="flex h-8 items-center gap-1.5 rounded-lg border border-border/60 px-3 text-[13px] text-muted-foreground transition-colors hover:text-foreground" />
                }
              >
                Sort by <span className="text-foreground">{SORT_LABEL[sort]}</span>
                <ChevronDown className="size-3.5" />
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="min-w-36">
                {(Object.keys(SORT_LABEL) as Sort[]).map((s) => (
                  <DropdownMenuItem key={s} onClick={() => setSort(s)}>
                    {SORT_LABEL[s]}
                  </DropdownMenuItem>
                ))}
              </DropdownMenuContent>
            </DropdownMenu>
            <Button onClick={() => openForm()} className="h-8">
              New task
            </Button>
          </div>
        </div>
        <p className="mt-1 text-[14px] text-muted-foreground">
          Run chats on a schedule or whenever you need them. Results land in
          your chats.
        </p>

        {showForm && (
          <div className="mt-6 rounded-2xl border border-border/60 bg-card/40 p-5">
            <div className="text-[15px] font-medium">New scheduled task</div>
            <div className="mt-4 space-y-4">
              <div>
                <label className="text-[13px] text-muted-foreground">Name</label>
                <Input
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="Daily brief"
                  className="mt-1.5"
                />
              </div>
              <div>
                <label className="text-[13px] text-muted-foreground">
                  What should Jarvis do?
                </label>
                <Textarea
                  value={prompt}
                  onChange={(e) => setPrompt(e.target.value)}
                  placeholder="Summarize the top AI news of the day…"
                  rows={3}
                  className="mt-1.5"
                />
              </div>
              <div>
                <label className="text-[13px] text-muted-foreground">When</label>
                <Input
                  value={when}
                  onChange={(e) => setWhen(e.target.value)}
                  placeholder='e.g. "every day at 9am", "weekdays at 8:30", "in 2 hours"'
                  className="mt-1.5"
                />
                <p
                  className={cn(
                    "mt-1.5 text-[12px]",
                    when.trim() && !parsedWhen
                      ? "text-destructive"
                      : "text-muted-foreground",
                  )}
                >
                  {when.trim()
                    ? (parsedWhen?.label ?? "Couldn't understand that schedule")
                    : "Plain English — daily, weekdays, weekly, once."}
                </p>
              </div>
            </div>
            <div className="mt-5 flex items-center justify-end gap-2">
              <Button variant="ghost" onClick={() => setShowForm(false)}>
                Cancel
              </Button>
              <Button
                onClick={create}
                disabled={saving || !parsedWhen || !name.trim() || !prompt.trim()}
              >
                {saving ? "Creating…" : "Create task"}
              </Button>
            </div>
          </div>
        )}

        <div className="relative mt-6">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground/60" />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search scheduled tasks…"
            className="w-full rounded-xl border border-border/60 bg-card/50 py-2.5 pl-10 pr-4 text-[14px] outline-none transition-colors placeholder:text-muted-foreground/60 focus:border-border focus:bg-card"
          />
        </div>

        {error && <p className="mt-4 text-[13px] text-destructive">{error}</p>}

        {tasks === null ? (
          <div className="flex justify-center py-24">
            <Loader2 className="size-5 animate-spin text-muted-foreground" />
          </div>
        ) : rows.length === 0 && !showForm ? (
          <div className="flex flex-col items-center py-20">
            <div className="flex size-16 items-center justify-center rounded-full border border-border/60">
              <AlarmClock className="size-7 text-muted-foreground" />
            </div>
            <p className="mt-6 text-[14px] text-muted-foreground">
              {q.trim()
                ? "No scheduled tasks match your search."
                : "Create your first scheduled task"}
            </p>
            {!q.trim() && (
              <div className="mt-5 flex items-center gap-2">
                {TEMPLATES.map((tpl) => (
                  <button
                    key={tpl.chip}
                    type="button"
                    onClick={() => router.push(`/chat?schedule=${tpl.slug}`)}
                    className="flex items-center gap-2 rounded-lg border border-border/60 bg-card/40 px-3.5 py-2 text-[13px] font-medium transition-colors hover:bg-card"
                  >
                    <tpl.icon className="size-4 text-muted-foreground" />
                    {tpl.chip}
                  </button>
                ))}
              </div>
            )}
          </div>
        ) : (
          <div className="mt-6 divide-y divide-border/40">
            {rows.map(({ t, next }) => (
              <div key={t.id} className="group flex items-center gap-3 py-3.5">
                <button
                  type="button"
                  onClick={() =>
                    t.last_conversation_id &&
                    router.push(`/chat/${t.last_conversation_id}`)
                  }
                  disabled={!t.last_conversation_id}
                  className="min-w-0 flex-1 text-left"
                  title={
                    t.last_conversation_id
                      ? "Open the latest run"
                      : "No runs yet"
                  }
                >
                  <div className="flex items-center gap-2">
                    <span className="truncate text-[14.5px] font-medium">
                      {t.name}
                    </span>
                    {t.paused && (
                      <span className="shrink-0 rounded-sm bg-muted px-1.5 py-px text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                        Paused
                      </span>
                    )}
                  </div>
                  <div className="mt-0.5 flex items-center gap-2 text-[12.5px] text-muted-foreground">
                    <span>{t.label}</span>
                    {!t.paused && (
                      <>
                        <span aria-hidden>·</span>
                        <span>{nextRunLabel(next)}</span>
                      </>
                    )}
                    {t.last_run_at && (
                      <>
                        <span aria-hidden>·</span>
                        <span>Last run {formatRelativeTime(t.last_run_at)}</span>
                      </>
                    )}
                  </div>
                </button>
                <div
                  className={cn(
                    "flex shrink-0 items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100",
                    busyId === t.id && "opacity-100",
                  )}
                >
                  <button
                    type="button"
                    onClick={() => runNow(t.id)}
                    disabled={busyId === t.id}
                    title="Run now"
                    aria-label="Run now"
                    className="flex size-8 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-accent/50 hover:text-foreground"
                  >
                    {busyId === t.id ? (
                      <Loader2 className="size-4 animate-spin" />
                    ) : (
                      <Play className="size-4" />
                    )}
                  </button>
                  <button
                    type="button"
                    onClick={() => togglePause(t)}
                    disabled={busyId === t.id}
                    title={t.paused ? "Resume" : "Pause"}
                    aria-label={t.paused ? "Resume" : "Pause"}
                    className="flex size-8 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-accent/50 hover:text-foreground"
                  >
                    {t.paused ? <Play className="size-4" /> : <Pause className="size-4" />}
                  </button>
                  <button
                    type="button"
                    onClick={() => remove(t.id)}
                    disabled={busyId === t.id}
                    title="Delete"
                    aria-label="Delete"
                    className="flex size-8 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-accent/50 hover:text-destructive"
                  >
                    <Trash2 className="size-4" />
                  </button>
                </div>
                <ChevronRight className="size-4 shrink-0 text-muted-foreground/40" />
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
