"use client";

import { useEffect, useRef, useState } from "react";
import {
  Cloud,
  ChevronDown,
  ChevronRight,
  Share2,
  PanelRight,
  ExternalLink,
  Pencil,
  Palette,
  FileText,
  Link2,
  Settings2,
  Archive,
  Trash2,
  Check,
  GitCompare,
  ListChecks,
  Columns2,
  Copy,
  Volume2,
  Square,
} from "lucide-react";

type CodeEvent = {
  cursor: number;
  type: string;
  payload: Record<string, unknown>;
  created_at: number;
};

// Worker runtime state from the events poll (PUT /worker, stored
// server-side). worker_status: 'idle' | 'running' | 'requires_action'.
// pending_action (external_metadata) carries the blocked tool call,
// including its raw input — needed to echo updatedInput on approve.
type WorkerInfo = {
  worker_status?: string;
  requires_action_details?: {
    tool_name?: string;
    action_description?: string;
    request_id?: string;
  } | null;
  external_metadata?: {
    pending_action?: {
      tool_name?: string;
      action_description?: string;
      request_id?: string;
      input?: Record<string, unknown>;
    } | null;
  } | null;
};

/** The permission prompt currently blocking the worker, if any. */
function pendingAction(w: WorkerInfo | null): {
  tool_name: string;
  action_description: string;
  request_id: string;
  input?: Record<string, unknown>;
} | null {
  if (!w || w.worker_status !== "requires_action") return null;
  const fromMeta = w.external_metadata?.pending_action;
  const fromDetails = w.requires_action_details;
  const merged = { ...(fromDetails ?? {}), ...(fromMeta ?? {}) };
  if (!merged.request_id) return null;
  return {
    tool_name: merged.tool_name ?? "tool",
    action_description: merged.action_description ?? "Run a tool",
    request_id: merged.request_id,
    input: fromMeta?.input,
  };
}

const MENU = [
  { icon: ExternalLink, label: "Open in", chord: "", sub: true },
  { icon: Pencil, label: "Rename", chord: "R", sub: false },
  { icon: Palette, label: "Color", chord: "", sub: true },
  { icon: FileText, label: "Transcript view", chord: "", sub: true },
  { icon: Link2, label: "Copy link", chord: "C", sub: false },
  { icon: Settings2, label: "Edit environment", chord: "", sub: false },
] as const;

function str(p: Record<string, unknown>, k: string): string | undefined {
  return typeof p[k] === "string" ? (p[k] as string) : undefined;
}

function timeAgo(ts?: number): string {
  if (!ts) return "";
  const s = Math.max(0, Math.floor((Date.now() - ts) / 1000));
  if (s < 60) return "just now";
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

// Per-message action bar (Copy / Read aloud / time), like claude.ai's hover
// toolbar. Copy + Read aloud are fully client-side (clipboard + the browser
// SpeechSynthesis API) — no backend. Pin-per-message is deferred (needs
// event-level state + a pinned view; the sidebar has session-level pin).
function MessageActions({ text, ts }: { text: string; ts: number }) {
  const [copied, setCopied] = useState(false);
  const [speaking, setSpeaking] = useState(false);

  const copy = () => {
    void navigator.clipboard?.writeText(text).catch(() => {});
    setCopied(true);
    setTimeout(() => setCopied(false), 1200);
  };

  const readAloud = () => {
    const synth = typeof window !== "undefined" ? window.speechSynthesis : null;
    if (!synth) return;
    if (speaking) {
      synth.cancel();
      setSpeaking(false);
      return;
    }
    synth.cancel();
    const u = new SpeechSynthesisUtterance(text);
    u.onend = () => setSpeaking(false);
    u.onerror = () => setSpeaking(false);
    synth.speak(u);
    setSpeaking(true);
  };

  // Stop narration if the message unmounts (session switch).
  useEffect(() => () => window.speechSynthesis?.cancel(), []);

  const btn = "flex size-6 items-center justify-center rounded text-muted-foreground/60 hover:bg-accent/40 hover:text-foreground";
  return (
    <div className="mt-1 flex items-center gap-0.5 pl-4">
      <button type="button" aria-label="Copy" title="Copy" onClick={copy} className={btn}>
        {copied ? <Check className="size-3.5 text-emerald-500" /> : <Copy className="size-3.5" />}
      </button>
      <button type="button" aria-label="Read aloud" title="Read aloud" onClick={readAloud} className={btn}>
        {speaking ? <Square className="size-3 fill-current" /> : <Volume2 className="size-3.5" />}
      </button>
      <span className="ml-1 text-[11px] text-muted-foreground/50">{timeAgo(ts)}</span>
    </div>
  );
}

type RenderItem =
  | { kind: "init"; key: string; steps: { cursor: number; text: string }[] }
  | { kind: "event"; event: CodeEvent };

// Fold runs of `status`/`system` events (the container init steps — "Set up a
// cloud container", "Cloned repository", …) into a single collapsible group.
// Non-status events pass through in order.
function foldEvents(events: CodeEvent[]): RenderItem[] {
  const out: RenderItem[] = [];
  for (const e of events) {
    if (e.type === "status" || e.type === "system") {
      const text =
        (typeof e.payload.status === "string" ? e.payload.status : "") ||
        "Session event";
      const last = out[out.length - 1];
      if (last && last.kind === "init") {
        last.steps.push({ cursor: e.cursor, text });
      } else {
        out.push({ kind: "init", key: `init-${e.cursor}`, steps: [{ cursor: e.cursor, text }] });
      }
    } else {
      out.push({ kind: "event", event: e });
    }
  }
  return out;
}

// Display text for SDK-shaped messages (type user/assistant from the CLI's
// CCR v2 transcript flush): message.content is a string or an array of
// blocks — show text blocks, name tool uses, skip tool results.
function sdkText(p: Record<string, unknown>): string | undefined {
  const msg = p.message as Record<string, unknown> | undefined;
  const content = msg?.content;
  if (typeof content === "string") return content.trim() || undefined;
  if (Array.isArray(content)) {
    const parts: string[] = [];
    for (const block of content as Array<Record<string, unknown>>) {
      if (block?.type === "text" && typeof block.text === "string") parts.push(block.text);
      else if (block?.type === "tool_use" && typeof block.name === "string") parts.push(`⚙ ${block.name}`);
    }
    const joined = parts.join("\n").trim();
    return joined || undefined;
  }
  if (p.type === "result" && typeof p.result === "string") return p.result.trim() || undefined;
  return undefined;
}

export function CodeSession({
  sessionId,
  repo,
  title,
  panels,
  onTogglePanel,
  onShare,
}: {
  sessionId: string;
  repo?: string | null;
  title?: string;
  panels: { diff: boolean; background: boolean; plan: boolean };
  onTogglePanel: (p: "diff" | "background" | "plan") => void;
  onShare: () => void;
}) {
  const [events, setEvents] = useState<CodeEvent[]>([]);
  const [waiting, setWaiting] = useState(true);
  const [menuOpen, setMenuOpen] = useState(false);
  const [layoutOpen, setLayoutOpen] = useState(false);
  const [sendError, setSendError] = useState<string | null>(null);
  const [worker, setWorker] = useState<WorkerInfo | null>(null);
  // In-flight assistant text (ephemeral stream_event snapshots relayed by
  // the events poll) — shows the reply streaming before the final message.
  const [live, setLive] = useState<string | null>(null);
  // request_ids the user already answered — hides the card instantly while
  // the CLI processes the response and clears requires_action server-side.
  const [answered, setAnswered] = useState<Set<string>>(new Set());
  // The container init steps (status events) collapse into one "Initialized
  // session" block, like claude.ai. Open while initializing; the user can
  // toggle. Auto-collapses once real output arrives (see below).
  const [initOpen, setInitOpen] = useState(true);
  const initAutoCollapsed = useRef(false);
  const cursorRef = useRef(0);
  const scrollRef = useRef<HTMLDivElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const layoutRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setEvents([]);
    cursorRef.current = 0;
    setWaiting(true);
    setWorker(null);
    setLive(null);
    setAnswered(new Set());
    setInitOpen(true);
    initAutoCollapsed.current = false;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const r = await fetch(`/api/bridge/v1/sessions/${sessionId}/events?since=${cursorRef.current}`);
        if (r.ok) {
          const j = (await r.json()) as {
            events: CodeEvent[];
            cursor: number;
            worker?: WorkerInfo | null;
            live?: string | null;
          };
          if (active && j.events?.length) {
            cursorRef.current = j.cursor;
            setEvents((prev) => [...prev, ...j.events]);
            if (j.events.some((e) => e.type !== "user_prompt")) setWaiting(false);
          }
          if (active) {
            setWorker(j.worker ?? null);
            setLive(j.live ?? null);
          }
        }
      } catch {
        /* transient */
      }
      if (active) timer = setTimeout(poll, 1500);
    };
    poll();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [sessionId]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [events.length, live]);

  // Collapse the init block once the assistant starts producing real output —
  // matches claude.ai (steps visible while initializing, tidy once running).
  // Once only, so a user who re-opens it keeps it open.
  useEffect(() => {
    if (initAutoCollapsed.current) return;
    const hasOutput = events.some(
      (e) => e.type !== "status" && e.type !== "system" && e.type !== "user_prompt" && e.type !== "user",
    );
    if (hasOutput) {
      initAutoCollapsed.current = true;
      setInitOpen(false);
    }
  }, [events]);

  useEffect(() => {
    if (!menuOpen) return;
    const onDown = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuOpen(false);
    };
    window.addEventListener("mousedown", onDown);
    return () => window.removeEventListener("mousedown", onDown);
  }, [menuOpen]);

  useEffect(() => {
    if (!layoutOpen) return;
    const onDown = (e: MouseEvent) => {
      if (layoutRef.current && !layoutRef.current.contains(e.target as Node)) setLayoutOpen(false);
    };
    window.addEventListener("mousedown", onDown);
    return () => window.removeEventListener("mousedown", onDown);
  }, [layoutOpen]);

  const post = (payload: Record<string, unknown>) =>
    fetch(`/api/bridge/v1/sessions/${sessionId}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }).then(async (r) => {
      if (!r.ok) {
        const j = (await r.json().catch(() => null)) as {
          error?: { message?: string };
        } | null;
        throw new Error(j?.error?.message ?? `HTTP ${r.status}`);
      }
    });

  const pending = pendingAction(worker);
  const running = worker?.worker_status === "running";

  const answerPermission = (
    action: NonNullable<ReturnType<typeof pendingAction>>,
    behavior: "allow" | "deny",
  ) => {
    setAnswered((prev) => new Set(prev).add(action.request_id));
    post({
      permission: {
        request_id: action.request_id,
        behavior,
        // Approve = run with the original input (pending_action.input); the
        // CLI treats updatedInput as a full replacement, so echoing it back
        // unchanged is the "yes, do that" answer.
        ...(behavior === "allow" && action.input
          ? { updated_input: action.input }
          : {}),
      },
    }).catch((err: unknown) => {
      setSendError(err instanceof Error ? err.message : String(err));
    });
  };

  return (
    <div className="flex h-full flex-col">
      {/* Breadcrumb header */}
      <div className="flex items-center justify-between px-4 py-2.5">
        <div className="relative flex items-center gap-1.5 text-[13px]" ref={menuRef}>
          <Cloud className="size-3.5 text-blue-400" />
          <span className="text-foreground/75">{repo ?? "repo"}</span>
          <span className="text-muted-foreground/60">/</span>
          <button
            type="button"
            onClick={() => setMenuOpen((o) => !o)}
            className="flex items-center gap-1 font-medium text-foreground hover:opacity-80"
          >
            {title ?? "New session"}
            <ChevronDown className="size-3.5 text-muted-foreground" />
          </button>

          {menuOpen && (
            <div className="absolute left-16 top-full z-50 mt-1 w-[230px] rounded-lg border border-border bg-card p-1 shadow-xl">
              {MENU.map((m) => (
                <button key={m.label} type="button" className="flex w-full items-center gap-2.5 rounded px-2.5 py-1.5 text-left text-[13px] text-foreground/90 hover:bg-accent/50">
                  <m.icon className="size-3.5 text-muted-foreground" />
                  <span className="flex-1">{m.label}</span>
                  {m.sub && <ChevronRight className="size-3.5 text-muted-foreground" />}
                  {m.chord && <span className="text-[11px] text-muted-foreground/60">{m.chord}</span>}
                </button>
              ))}
              <div className="my-1 border-t border-border/50" />
              <button type="button" className="flex w-full items-center gap-2.5 rounded px-2.5 py-1.5 text-left text-[13px] text-foreground/90 hover:bg-accent/50">
                <Archive className="size-3.5 text-muted-foreground" />
                <span className="flex-1">Archive</span>
                <span className="text-[11px] text-muted-foreground/60">A</span>
              </button>
              <button type="button" className="flex w-full items-center gap-2.5 rounded px-2.5 py-1.5 text-left text-[13px] text-red-500 hover:bg-red-500/10">
                <Trash2 className="size-3.5" />
                <span className="flex-1">Delete</span>
                <span className="text-[11px] text-red-500/60">D</span>
              </button>
            </div>
          )}
        </div>
        <div className="relative flex items-center gap-1" ref={layoutRef}>
          <button type="button" onClick={onShare} aria-label="Share session" className="flex size-7 items-center justify-center rounded-md text-muted-foreground hover:bg-accent/50 hover:text-foreground">
            <Share2 className="size-4" />
          </button>
          <button type="button" onClick={() => setLayoutOpen((o) => !o)} aria-label="Panels" className="flex size-7 items-center justify-center rounded-md text-muted-foreground hover:bg-accent/50 hover:text-foreground">
            <Columns2 className="size-4" />
          </button>
          {layoutOpen && (
            <div className="absolute right-0 top-full z-50 mt-1 w-[215px] rounded-lg border border-border bg-card p-1 shadow-xl">
              {([
                { key: "diff", label: "Diff", icon: GitCompare, chord: "Ctrl+⇧+D" },
                { key: "background", label: "Background tasks", icon: Columns2, chord: "" },
                { key: "plan", label: "Plan", icon: ListChecks, chord: "" },
              ] as const).map((it) => (
                <button
                  key={it.key}
                  type="button"
                  onClick={() => onTogglePanel(it.key)}
                  className="flex w-full items-center gap-2.5 rounded px-2.5 py-1.5 text-left text-[13px] text-foreground/90 hover:bg-accent/50"
                >
                  <it.icon className="size-3.5 text-muted-foreground" />
                  <span className="flex-1">{it.label}</span>
                  {panels[it.key] && <Check className="size-3.5 text-primary" />}
                  {it.chord && <span className="text-[11px] text-muted-foreground/60">{it.chord}</span>}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Messages */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto px-6 py-4">
        <div className="mx-auto max-w-3xl space-y-3">
          {foldEvents(events).map((item) => {
            // Container init / status run → one collapsible "Initialized
            // session" block (claude.ai-style), instead of N bare rows.
            if (item.kind === "init") {
              const failed = item.steps.some((s) => s.text.startsWith("✗"));
              return (
                <div key={item.key} className="rounded-xl border border-border/50 bg-card/40">
                  <button
                    type="button"
                    onClick={() => setInitOpen((o) => !o)}
                    className="flex w-full items-center gap-1.5 px-3 py-2 text-left text-[13px] text-foreground/80 hover:text-foreground"
                  >
                    {initOpen ? <ChevronDown className="size-3.5 text-muted-foreground" /> : <ChevronRight className="size-3.5 text-muted-foreground" />}
                    <span className="font-medium">{failed ? "Session failed to start" : "Initialized session"}</span>
                  </button>
                  {initOpen && (
                    <div className="space-y-1 px-3 pb-2.5 pl-8">
                      {item.steps.map((s) => (
                        <div key={s.cursor} className="text-[12.5px] leading-relaxed text-muted-foreground">
                          {s.text}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              );
            }
            const e = item.event;
            if (e.type === "user_prompt" || e.type === "user") {
              const prompt = str(e.payload, "prompt") ?? sdkText(e.payload);
              // SDK user messages with only tool_result blocks carry no
              // human text — skip rather than render an empty bubble.
              if (!prompt) return null;
              return (
                <div key={e.cursor} className="flex justify-end">
                  <div className="max-w-[80%] whitespace-pre-wrap break-words rounded-2xl bg-accent/40 px-3.5 py-1.5 text-[13px] text-foreground">
                    {prompt}
                  </div>
                </div>
              );
            }
            const text = str(e.payload, "text") ?? str(e.payload, "content") ?? str(e.payload, "message") ?? sdkText(e.payload);
            return (
              <div key={e.cursor} className="group/msg flex flex-col">
                <div className="flex gap-2.5">
                  <span className="mt-1.5 size-1.5 shrink-0 rounded-full bg-amber-500" />
                  <div className="min-w-0 whitespace-pre-wrap break-words text-[13px] text-foreground/90">
                    {text ?? <span className="text-muted-foreground/70">{e.type}</span>}
                  </div>
                </div>
                {/* Action bar — only for messages with real text. Visible on
                    hover (always-on once narration starts via its own state). */}
                {text && (
                  <div className="opacity-0 transition-opacity group-hover/msg:opacity-100 focus-within:opacity-100">
                    <MessageActions text={text} ts={e.created_at} />
                  </div>
                )}
              </div>
            );
          })}

          {pending && !answered.has(pending.request_id) && (
            <div className="rounded-xl border border-amber-500/40 bg-amber-500/10 p-3">
              <p className="text-[13px] font-medium text-foreground">
                Permission needed: <span className="font-mono">{pending.tool_name}</span>
              </p>
              <p className="mt-0.5 text-[12px] text-muted-foreground">{pending.action_description}</p>
              <div className="mt-2 flex gap-2">
                <button
                  type="button"
                  onClick={() => answerPermission(pending, "allow")}
                  className="rounded-md bg-primary px-3 py-1 text-[12px] font-medium text-primary-foreground"
                >
                  Allow
                </button>
                <button
                  type="button"
                  onClick={() => answerPermission(pending, "deny")}
                  className="rounded-md border border-border px-3 py-1 text-[12px] text-foreground hover:bg-accent/40"
                >
                  Deny
                </button>
              </div>
            </div>
          )}

          {live && (
            <div className="flex gap-2.5">
              <span className="mt-1.5 size-1.5 shrink-0 animate-pulse rounded-full bg-orange-500" />
              <div className="min-w-0 whitespace-pre-wrap break-words text-[13px] text-foreground/80">
                {live}
              </div>
            </div>
          )}

          {(waiting || running) && !live && (
            <div className="flex items-center gap-2 pt-1 text-orange-500">
              <span className="inline-block animate-pulse text-[18px] leading-none">✳</span>
            </div>
          )}
        </div>
      </div>

      {/* Session controls — the text input lives in the page-level
          CodeComposer (one composer for both new-task and session modes);
          this bar only surfaces Stop while running and send errors. */}
      {(running || sendError) && (
        <div className="border-t border-border/60 px-6 py-2">
          <div className="mx-auto flex max-w-3xl items-center gap-3">
            {running && (
              <button
                type="button"
                onClick={() => {
                  post({ interrupt: true }).catch((err: unknown) => {
                    setSendError(err instanceof Error ? err.message : String(err));
                  });
                }}
                className="rounded-lg border border-border px-3 py-1.5 text-[13px] text-foreground hover:bg-accent/40"
              >
                Stop
              </button>
            )}
            {sendError && <p className="text-[12px] text-red-500">{sendError}</p>}
          </div>
        </div>
      )}
    </div>
  );
}
