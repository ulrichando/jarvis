"use client";

import { useState } from "react";
import { ChevronDown, Sun, MousePointerClick, Loader2, ArrowUp, Plus } from "lucide-react";
import { Switch } from "@/components/ui/switch";
import { DispatchOutputsTray } from "./dispatch-outputs-tray";

// A machine the task can be dispatched to. Structurally a subset of the /code
// page's Machine — page passes its (richer) rows straight in.
type Machine = {
  environment_id: string;
  machine_name: string;
  worker_type: string;
  git_repo_url: string | null;
  online: boolean;
};

const EXAMPLES = [
  "Fix the failing CI on my latest PR and push the fix.",
  "Update the project's dependencies and run the test suite.",
  "Comb the repo for every TODO and summarise what's left.",
];

// Faint graph-paper grid, matching Anthropic's Dispatch canvas.
const GRID_BG: React.CSSProperties = {
  backgroundImage:
    "linear-gradient(to right, rgba(255,255,255,0.025) 1px, transparent 1px), linear-gradient(to bottom, rgba(255,255,255,0.025) 1px, transparent 1px)",
  backgroundSize: "34px 34px",
};

// The Dispatch landing on the /code surface (a third view, like Routines).
// Two-pane over a grid canvas, matching Anthropic's Dispatch: a borderless left
// controls rail (info + the two toggles + target + Outputs) and a main area with
// a warm greeting and a bottom pill composer. Assign a task to your always-on
// desktop and check in from your phone — the session runs on your local /code
// worker, badged "Dispatch", and pings your phone when it finishes / needs you.
export function DispatchView({
  machines,
  selected,
  onPickMachine,
  model,
  recentDispatchSessionId,
  onOpenSession,
  onReloadSessions,
  onExit,
}: {
  machines: Machine[] | null;
  selected: Machine | null;
  onPickMachine: (environmentId: string) => void;
  model: string;
  /** Newest Dispatch-origin session, for the Outputs tray (or null). */
  recentDispatchSessionId: string | null;
  onOpenSession: (id: string) => void;
  onReloadSessions: () => void;
  /** Leave Dispatch, back to the Code sessions view (the ⌄ switcher). */
  onExit?: () => void;
}) {
  const [input, setInput] = useState("");
  const [keepAwake, setKeepAwake] = useState(false);
  // Default OFF → permission_mode 'default' (ask). A phone-initiated task on the
  // local box shouldn't run unattended unless you opt in.
  const [allowAll, setAllowAll] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const target = selected ?? (machines && machines[0]) ?? null;

  const submit = async () => {
    const prompt = input.trim();
    if (!prompt || busy) return;
    if (!target) {
      setError("Connect a machine first — run /remote-control on your always-on desktop.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const r = await fetch("/api/bridge/v1/dispatch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          environment_id: target.environment_id,
          prompt,
          keep_awake: keepAwake,
          allow_all_actions: allowAll,
          model,
        }),
      });
      if (r.ok) {
        const j = (await r.json()) as { session_id: string };
        setInput("");
        onReloadSessions();
        onOpenSession(j.session_id);
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
    <div className="flex h-full min-h-0 flex-col md:flex-row" style={GRID_BG}>
      {/* ── Left controls rail (borderless — floats on the canvas) ──────── */}
      <aside className="shrink-0 space-y-5 px-5 py-5 md:w-[280px] md:overflow-y-auto">
        <button
          type="button"
          onClick={onExit}
          title="Back to Code sessions"
          className="flex items-center gap-1 text-[15px] font-semibold text-foreground transition-colors hover:text-foreground/75"
        >
          Dispatch
          <ChevronDown className="size-4 text-muted-foreground" />
        </button>

        <p className="rounded-xl border border-border/40 bg-card/40 px-3.5 py-3 text-[12.5px] leading-relaxed text-muted-foreground">
          Dispatch tasks to Jarvis and check in from your phone or computer — all
          in one seamless conversation.
        </p>

        {/* Toggles */}
        <div className="space-y-3.5">
          <label className="flex cursor-pointer items-center gap-2.5">
            <Sun className="size-4 shrink-0 text-muted-foreground" />
            <span className="flex-1 text-[13px] text-foreground">
              Keep awake
              <span className="block text-[11px] leading-snug text-muted-foreground/70">
                Holds a sleep inhibitor on the machine while the task runs.
              </span>
            </span>
            <Switch checked={keepAwake} onCheckedChange={setKeepAwake} size="sm" />
          </label>
          <label className="flex cursor-pointer items-center gap-2.5">
            <MousePointerClick className="size-4 shrink-0 text-muted-foreground" />
            <span className="flex-1 text-[13px] text-foreground">Allow all browser actions</span>
            <Switch checked={allowAll} onCheckedChange={setAllowAll} size="sm" />
          </label>
        </div>

        {/* Target machine — only shown when there's a choice to make (0 or 2+).
            A single machine is auto-targeted, so the rail stays clean like the
            reference. */}
        {!(machines && machines.length === 1) && (
          <div>
            <div className="mb-1 text-[11px] text-muted-foreground/60">Runs on</div>
            {machines === null ? (
              <div className="text-[12.5px] text-muted-foreground/60">Loading machines…</div>
            ) : machines.length === 0 ? (
              <div className="text-[12px] leading-relaxed text-muted-foreground/70">
                No machine — run <code className="font-mono text-[11px]">/remote-control</code> on
                your desktop.
              </div>
            ) : (
              <select
                value={target?.environment_id ?? ""}
                onChange={(e) => onPickMachine(e.target.value)}
                className="-ml-1 max-w-full truncate rounded-md bg-transparent px-1 py-0.5 text-[13px] text-foreground/90 outline-none hover:bg-accent/40 focus:bg-accent/40"
              >
                {machines.map((m) => (
                  <option key={m.environment_id} value={m.environment_id}>
                    {m.machine_name}
                    {m.online ? "" : " (offline)"}
                  </option>
                ))}
              </select>
            )}
          </div>
        )}

        {/* Outputs */}
        <div>
          <div className="mb-1.5 text-[12px] text-muted-foreground/70">Outputs</div>
          <DispatchOutputsTray
            sessionId={recentDispatchSessionId}
            onOpen={
              recentDispatchSessionId
                ? () => onOpenSession(recentDispatchSessionId)
                : undefined
            }
          />
        </div>
      </aside>

      {/* ── Main area: greeting + bottom pill composer ─────────────────── */}
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <div className="flex flex-1 items-center overflow-y-auto">
          <div className="mx-auto w-full max-w-xl px-6 py-10">
            <p className="text-[15px] leading-relaxed text-foreground/90">
              Hey, glad you&rsquo;re here. Tell me what&rsquo;s on your plate — no
              ask is too big or small. You could ask me to:
            </p>
            <ul className="mt-4 space-y-2">
              {EXAMPLES.map((ex) => (
                <li key={ex}>
                  <button
                    type="button"
                    onClick={() => setInput(ex)}
                    className="group flex w-full items-start gap-2.5 rounded-lg px-2 py-1.5 text-left text-[14px] leading-relaxed text-muted-foreground transition-colors hover:bg-accent/25 hover:text-foreground"
                  >
                    <span className="mt-2 size-1 shrink-0 rounded-full bg-muted-foreground/40 group-hover:bg-primary" />
                    <span>{ex}</span>
                  </button>
                </li>
              ))}
            </ul>
            <p className="mt-5 text-[13px] leading-relaxed text-muted-foreground/75">
              You can also pick this up from your phone — open the session on
              0wlan.com and check in from anywhere.
            </p>
          </div>
        </div>

        {/* Composer — a single pill floating at the bottom. */}
        <div className="px-4 pb-5 pt-2">
          <div className="mx-auto w-full max-w-2xl">
            {error && <div className="mb-2 px-2 text-[12px] text-red-500">{error}</div>}
            <div className="flex items-end gap-2 rounded-[26px] border border-border/60 bg-card/60 px-3 py-2 shadow-sm backdrop-blur focus-within:border-border">
              <Plus className="mb-1.5 size-5 shrink-0 text-muted-foreground/70" />
              <textarea
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
                    e.preventDefault();
                    void submit();
                  }
                }}
                rows={1}
                placeholder="Ask Jarvis anything"
                className="max-h-40 flex-1 resize-none bg-transparent py-1.5 text-[14px] text-foreground placeholder:text-muted-foreground/50 outline-none"
              />
              <button
                type="button"
                onClick={() => void submit()}
                disabled={busy || !input.trim()}
                aria-label="Dispatch"
                className="flex size-8 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground transition-opacity disabled:opacity-30"
              >
                {busy ? (
                  <Loader2 className="size-4 animate-spin" />
                ) : (
                  <ArrowUp className="size-4" strokeWidth={2.5} />
                )}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
