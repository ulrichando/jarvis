"use client";

import { Component, type ComponentType, type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { Markdown } from "@/components/markdown/markdown";
import { ToolCall, type ToolResult, type ToolUse } from "./tool-call";
import {
  Cloud,
  ChevronDown,
  ChevronRight,
  Share2,
  PanelRight,
  ExternalLink,
  Pencil,
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
  Play,
  Square,
  Pin,
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

// One row of the session-title dropdown. Every item is a real action (no
// decorative stubs) — `sub` shows a chevron for expandable groups, `active`
// a check for the current transcript view, `danger` for Delete.
function HeaderMenuItem({
  icon: Icon,
  label,
  chord,
  sub,
  active,
  danger,
  disabled,
  onClick,
}: {
  icon: ComponentType<{ className?: string }>;
  label: string;
  chord?: string;
  sub?: boolean;
  active?: boolean;
  danger?: boolean;
  disabled?: boolean;
  onClick?: () => void;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className={`flex w-full items-center gap-2.5 rounded px-2.5 py-1.5 text-left text-[13px] disabled:opacity-40 ${danger ? "text-red-500 hover:bg-red-500/10" : "text-foreground/90 hover:bg-accent/50"}`}
    >
      <Icon className={`size-3.5 ${danger ? "" : "text-muted-foreground"}`} />
      <span className="flex-1">{label}</span>
      {active && <Check className="size-3.5 text-primary" />}
      {sub && <ChevronRight className="size-3.5 text-muted-foreground" />}
      {chord && <span className="text-[11px] text-muted-foreground/60">{chord}</span>}
    </button>
  );
}

function str(p: Record<string, unknown>, k: string): string | undefined {
  return typeof p[k] === "string" ? (p[k] as string) : undefined;
}

// A render error in one message's Markdown (e.g. shiki choking on a huge
// expanded code block) used to blank the whole route — the app has no global
// error boundary. Isolate each message: on error, fall back to plain text so
// the rest of the chat survives.
class MessageBoundary extends Component<
  { children: ReactNode; fallback: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    return this.state.failed ? this.props.fallback : this.props.children;
  }
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

// Per-message action bar (Copy / Pin / Read aloud / time), like claude.ai's
// hover toolbar. Copy + Read aloud are fully client-side (clipboard + the
// browser SpeechSynthesis API). Pin is server-synced (session_message_pins) so
// it survives across devices/browsers; the pinned set is fetched once by the
// parent and passed down.
function MessageActions({
  text,
  ts,
  uuid,
  sessionId,
  pinnedUuids,
}: {
  text: string;
  ts: number;
  uuid?: string;
  sessionId?: string;
  pinnedUuids?: Set<string>;
}) {
  const [copied, setCopied] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  const [pinned, setPinned] = useState(false);

  useEffect(() => {
    if (uuid && pinnedUuids) setPinned(pinnedUuids.has(uuid));
  }, [uuid, pinnedUuids]);

  const copy = () => {
    void navigator.clipboard?.writeText(text).catch(() => {});
    setCopied(true);
    setTimeout(() => setCopied(false), 1200);
  };

  const togglePin = () => {
    if (!uuid) return;
    const next = !pinned;
    setPinned(next);
    if (sessionId) {
      fetch(`/api/bridge/v1/sessions/${sessionId}/pins`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ uuid, pinned: next }),
      }).catch(() => {});
    }
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
      <button type="button" aria-label={pinned ? "Unpin" : "Pin"} title={pinned ? "Unpin" : "Pin"} onClick={togglePin} className={`${btn} ${pinned ? "!text-primary" : ""}`}>
        <Pin className={`size-3.5 -rotate-45 ${pinned ? "fill-current" : ""}`} />
      </button>
      <button type="button" aria-label="Read aloud" title="Read aloud" onClick={readAloud} className={`${btn} ${speaking ? "!text-primary" : ""}`}>
        {speaking ? <Square className="size-3 fill-current" /> : <Play className="size-3.5" />}
      </button>
      <span className="ml-1 text-[11px] text-muted-foreground/50">{timeAgo(ts)}</span>
    </div>
  );
}

// CLI display-tag wrappers (slash-command output etc.). A model switch comes
// back as <local-command-stdout>Set model to …</local-command-stdout>; we want
// the inner text shown as a quiet system line, not a raw-tagged user bubble.
const CMD_TAG_RE = /<\/?(?:local-command-stdout|local-command-stderr|command-name|command-message|command-args|bash-stdout|bash-stderr|system-reminder)>/g;
/** If the text is a command-output blob, return its inner text; else null. */
function commandOutput(text: string): string | null {
  if (!/<local-command-(?:stdout|stderr)>/.test(text)) return null;
  const inner = text.replace(CMD_TAG_RE, "").trim();
  return inner || null;
}
function stripTags(text: string): string {
  return text.replace(CMD_TAG_RE, "").trim();
}

// Drop replayed duplicates: a model switch reconnects the worker, which
// replays the session from seq 0 — without this the greeting and the init
// steps render twice (see the "two Initialized session blocks" bug). Dedupe
// by message uuid when present, else by status text.
function dedupeEvents(events: CodeEvent[]): CodeEvent[] {
  const seen = new Set<string>();
  const out: CodeEvent[] = [];
  for (const e of events) {
    const uuid = typeof e.payload.uuid === "string" ? (e.payload.uuid as string) : null;
    let key: string | null = null;
    if (e.type === "status" || e.type === "system") {
      // Init steps repeat verbatim on a re-init — dedupe by text.
      key = `s:${String(e.payload.status ?? "")}`;
    } else if (e.type === "assistant" || e.type === "result") {
      // A worker reconnect (e.g. on model switch) replays the prompt, so the
      // greeting is re-generated with a DIFFERENT uuid but identical text —
      // dedupe by content, not uuid, so it renders once.
      const content = assistantDedupeKey(e.payload) || sdkText(e.payload) || str(e.payload, "text") || "";
      key = content ? `a:${content}` : null;
    } else if (uuid) {
      key = `u:${uuid}`;
    }
    if (key) {
      if (seen.has(key)) continue;
      seen.add(key);
    }
    out.push(e);
  }
  return out;
}

type RenderItem =
  | { kind: "init"; key: string; steps: { cursor: number; text: string }[] }
  | { kind: "event"; event: CodeEvent };

// Fold ALL `status`/`system` events (container init steps) into ONE init
// group, anchored at the first one. A model switch can trigger a second init
// run; collapsing into a single group keeps one "Initialized session" line
// instead of two. Non-status events pass through in order.
function foldEvents(events: CodeEvent[]): RenderItem[] {
  const out: RenderItem[] = [];
  let init: Extract<RenderItem, { kind: "init" }> | null = null;
  for (const e of events) {
    if (e.type === "status" || e.type === "system") {
      const text =
        (typeof e.payload.status === "string" ? e.payload.status : "") ||
        "Session event";
      if (!init) {
        init = { kind: "init", key: "init", steps: [] };
        out.push(init);
      }
      init.steps.push({ cursor: e.cursor, text });
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

// Parse an assistant SDK message into ordered render items: text blocks become
// prose, tool_use blocks become inline tool cards (so a file Write shows its
// path + code, Bash shows the command + output — claude.ai/code-style — rather
// than the old flattened "⚙ Write"). String content (rare) is one text item.
type AssistantItem =
  | { kind: "text"; text: string }
  | { kind: "tool"; use: ToolUse };

function assistantItems(p: Record<string, unknown>): AssistantItem[] {
  const msg = p.message as Record<string, unknown> | undefined;
  const content = msg?.content;
  if (typeof content === "string") {
    const t = content.trim();
    return t ? [{ kind: "text", text: t }] : [];
  }
  if (!Array.isArray(content)) return [];
  const items: AssistantItem[] = [];
  for (const block of content as Array<Record<string, unknown>>) {
    if (block?.type === "text" && typeof block.text === "string") {
      if (block.text.trim()) items.push({ kind: "text", text: block.text });
    } else if (block?.type === "tool_use" && typeof block.name === "string") {
      items.push({
        kind: "tool",
        use: {
          id: typeof block.id === "string" ? block.id : "",
          name: block.name,
          input:
            block.input && typeof block.input === "object"
              ? (block.input as Record<string, unknown>)
              : {},
        },
      });
    }
  }
  return items;
}

// Replay-dedupe key for an assistant message: its text + each tool call's
// name AND input. Keying on the full content (not just the tool NAME, which
// `sdkText` collapses to) means two distinct Writes don't dedupe each other,
// while an exact worker-replay of the same turn still does.
function assistantDedupeKey(p: Record<string, unknown>): string {
  return assistantItems(p)
    .map((it) =>
      it.kind === "text"
        ? `t:${it.text}`
        : `u:${it.use.name}:${JSON.stringify(it.use.input)}`,
    )
    .join("|");
}

// A tool_result block's `content` is a string OR an array of {type:'text',text}.
function toolResultText(content: unknown): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return (content as Array<unknown>)
      .map((b) =>
        typeof b === "string"
          ? b
          : b && typeof b === "object" && typeof (b as Record<string, unknown>).text === "string"
            ? ((b as Record<string, unknown>).text as string)
            : "",
      )
      .join("")
      .trim();
  }
  return "";
}

// tool_use_id → its result text + error flag, gathered from the `user`
// messages that carry tool_result blocks. Lets each ToolCall show its output.
function collectToolResults(events: CodeEvent[]): Map<string, ToolResult> {
  const m = new Map<string, ToolResult>();
  for (const e of events) {
    if (e.type !== "user" && e.type !== "result") continue;
    const content = (e.payload.message as Record<string, unknown> | undefined)?.content;
    if (!Array.isArray(content)) continue;
    for (const b of content as Array<Record<string, unknown>>) {
      if (b?.type === "tool_result" && typeof b.tool_use_id === "string") {
        m.set(b.tool_use_id, {
          text: toolResultText(b.content),
          isError: b.is_error === true,
        });
      }
    }
  }
  return m;
}

export function CodeSession({
  sessionId,
  repo,
  repoFull,
  title,
  panels,
  onTogglePanel,
  onShare,
  onRunningChange,
  onMutated,
  onEditEnvironment,
  sendNonce,
}: {
  sessionId: string;
  repo?: string | null;
  /** Full owner/name (not the short display name) for "Open on GitHub". */
  repoFull?: string | null;
  title?: string;
  panels: { diff: boolean; background: boolean; plan: boolean };
  onTogglePanel: (p: "diff" | "background" | "plan") => void;
  onShare: () => void;
  /** Report worker run state up so the composer can show a stop button. */
  onRunningChange?: (running: boolean) => void;
  /** Header-menu mutated the session (rename keeps it open; archive/delete
   *  remove it) — parent refreshes the list + deselects as appropriate. */
  onMutated?: (kind: "rename" | "archive" | "delete") => void;
  /** Header-menu "Edit environment" → parent opens the env-config modal. */
  onEditEnvironment?: () => void;
  /** Bumped by the composer on every send to this session — lets the view
   *  show the thinking indicator + fast-poll at once, instead of waiting for
   *  the idle (~2.5s) poll to notice (which read as "nothing, then sudden"). */
  sendNonce?: number;
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
  // Cheap +/- summary for the header indicator (full diff lives in the panel).
  const [diffStat, setDiffStat] = useState<{ adds: number; dels: number } | null>(null);
  // Server-synced pinned message uuids (fetched once; passed to MessageActions).
  const [pinnedUuids, setPinnedUuids] = useState<Set<string>>(new Set());
  // The container init steps (status events) collapse into one "Initialized
  // session" block, like claude.ai. Open while initializing; the user can
  // toggle. Auto-collapses once real output arrives (see below).
  const [initOpen, setInitOpen] = useState(true);
  // Transcript density (claude.ai/code Normal/Verbose/Summary), persisted.
  const [viewMode, setViewMode] = useState<"normal" | "verbose" | "summary">("normal");
  // tool_use_id → result, so each inline tool call can show its output.
  const toolResults = useMemo(() => collectToolResults(events), [events]);
  useEffect(() => {
    try {
      const v = localStorage.getItem("jarvis.code.viewMode");
      if (v === "verbose" || v === "summary" || v === "normal") setViewMode(v);
    } catch {
      /* no localStorage */
    }
  }, []);
  const initAutoCollapsed = useRef(false);
  const cursorRef = useRef(0);
  // Lets a composer send wake the poll loop immediately + flag "awaiting first
  // output" so the thinking indicator shows now, not on the next idle tick.
  const pokeRef = useRef<() => void>(() => {});
  const pendingSendRef = useRef(false);
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
    pendingSendRef.current = false;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    // Adaptive cadence: poll fast (~400ms) while the agent is producing
    // output — new events, a live stream, or worker still running — so
    // replies appear promptly; back off to ~2.5s when idle to avoid
    // hammering. (A flat 1.5s made every reply feel up to 1.5s late.)
    const FAST_MS = 400;
    const IDLE_MS = 2500;
    const poll = async () => {
      let busyNow = false;
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
            // Real (non-echo) output started → stop "thinking" and let the
            // normal busy signals drive cadence from here.
            if (j.events.some((e) => e.type !== "user_prompt")) {
              setWaiting(false);
              pendingSendRef.current = false;
            }
            busyNow = true;
          }
          if (active) {
            setWorker(j.worker ?? null);
            setLive(j.live ?? null);
          }
          if (j.worker?.worker_status === "running" || j.live) busyNow = true;
        }
      } catch {
        /* transient */
      }
      // Stay on fast cadence between "user just sent" and the first real
      // output, so the indicator + reply show promptly (not on the 2.5s tick).
      if (pendingSendRef.current) busyNow = true;
      if (active) timer = setTimeout(poll, busyNow ? FAST_MS : IDLE_MS);
    };
    // Let a composer send (sendNonce) wake the loop immediately.
    pokeRef.current = () => {
      clearTimeout(timer);
      poll();
    };
    poll();
    return () => {
      active = false;
      clearTimeout(timer);
      pokeRef.current = () => {};
    };
  }, [sessionId]);

  // A composer send bumps sendNonce → show the thinking indicator at once and
  // wake the poll, instead of waiting up to 2.5s for the idle tick to notice.
  const sendNonceSeen = useRef(sendNonce);
  useEffect(() => {
    if (sendNonce === undefined || sendNonce === sendNonceSeen.current) return;
    sendNonceSeen.current = sendNonce;
    pendingSendRef.current = true;
    setWaiting(true);
    pokeRef.current();
    // On first send, ask for notification permission so we can ping you when a
    // backgrounded session finishes its turn or needs input.
    try {
      if (typeof Notification !== "undefined" && Notification.permission === "default") {
        void Notification.requestPermission().catch(() => {});
      }
    } catch {
      /* unsupported */
    }
  }, [sendNonce]);

  // Pinned message set (server-synced), fetched once per session.
  useEffect(() => {
    let active = true;
    fetch(`/api/bridge/v1/sessions/${sessionId}/pins`)
      .then((r) => (r.ok ? r.json() : null))
      .then((j: { uuids?: string[] } | null) => {
        if (active && j?.uuids) setPinnedUuids(new Set(j.uuids));
      })
      .catch(() => {});
    return () => {
      active = false;
    };
  }, [sessionId]);

  // Header +/- indicator: a cheap summary-only diff poll (skips the full diff).
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const load = async () => {
      try {
        const r = await fetch(`/api/bridge/v1/sessions/${sessionId}/diff?summary=1`);
        if (r.ok && active) {
          const stat = ((await r.json()) as { stat?: string }).stat ?? "";
          const adds = Number(/(\d+) insertion/.exec(stat)?.[1] ?? 0);
          const dels = Number(/(\d+) deletion/.exec(stat)?.[1] ?? 0);
          setDiffStat(adds || dels ? { adds, dels } : null);
        }
      } catch {
        /* transient */
      }
      if (active) timer = setTimeout(load, 5000);
    };
    load();
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

  // ── Session-title dropdown actions ──────────────────────────────────────
  // Every item is wired (mirrors the sidebar kebab: PATCH/DELETE + clipboard).
  // `sub` tracks which group ("Open in" / "Transcript view") is expanded.
  const [sub, setSub] = useState<null | "openin" | "view">(null);
  useEffect(() => {
    if (!menuOpen) setSub(null);
  }, [menuOpen]);
  const setView = (m: "normal" | "verbose" | "summary") => {
    setViewMode(m);
    try {
      localStorage.setItem("jarvis.code.viewMode", m);
    } catch {
      /* no localStorage */
    }
  };
  const mutateSession = (init: RequestInit) =>
    fetch(`/api/bridge/v1/sessions/${sessionId}`, init).catch(() => {});
  const renameSession = async () => {
    const next = window.prompt("Rename session", title ?? "");
    setMenuOpen(false);
    if (next === null || !next.trim() || next.trim() === (title ?? "")) return;
    await mutateSession({
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: next.trim() }),
    });
    onMutated?.("rename");
  };
  const archiveSession = async () => {
    setMenuOpen(false);
    await mutateSession({
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ archived: true }),
    });
    onMutated?.("archive");
  };
  const deleteSession = async () => {
    setMenuOpen(false);
    if (!window.confirm("Delete this session permanently? This cannot be undone.")) return;
    await mutateSession({ method: "DELETE" });
    onMutated?.("delete");
  };
  const copyLink = () => {
    void navigator.clipboard
      ?.writeText(`${window.location.origin}/code/session_${sessionId}`)
      .catch(() => {});
    setMenuOpen(false);
  };
  const copySessionId = () => {
    void navigator.clipboard?.writeText(sessionId).catch(() => {});
    setMenuOpen(false);
  };
  const openOnGitHub = () => {
    setMenuOpen(false);
    if (repoFull && repoFull.includes("/")) {
      window.open(`https://github.com/${repoFull}`, "_blank", "noopener");
    }
  };
  const editEnvironment = () => {
    setMenuOpen(false);
    onEditEnvironment?.();
  };

  const pending = pendingAction(worker);
  const running = worker?.worker_status === "running";
  // The composer's Stop button (and Esc-to-stop) track the SAME "busy" signal
  // as the thinking indicator — so Stop shows the moment a turn is loading, not
  // only once the worker flips to worker_status==="running" (which lags the
  // container launch + the send→running gap). `waiting` covers send→first
  // output; `running` covers the rest of the turn.
  const busy = waiting || running;
  useEffect(() => {
    onRunningChange?.(busy);
  }, [busy, onRunningChange]);

  // Desktop notification when a turn ends or the session needs input, but only
  // if the tab is backgrounded (claude.ai/code notifies you to come back).
  const prevRunningRef = useRef(false);
  useEffect(() => {
    const was = prevRunningRef.current;
    prevRunningRef.current = running;
    const needsYou = (was && !running) || pending;
    if (
      needsYou &&
      typeof document !== "undefined" &&
      document.hidden &&
      typeof Notification !== "undefined" &&
      Notification.permission === "granted"
    ) {
      try {
        new Notification("Jarvis /code", {
          body: pending ? "A session needs your approval." : "A session finished its turn.",
        });
      } catch {
        /* ignore */
      }
    }
  }, [running, pending]);

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
    <div className="flex min-h-0 flex-1 flex-col">
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
              <HeaderMenuItem
                icon={ExternalLink}
                label="Open in"
                sub
                onClick={() => setSub((s) => (s === "openin" ? null : "openin"))}
              />
              {sub === "openin" && (
                <div className="ml-3 border-l border-border/40 pl-1">
                  <HeaderMenuItem
                    icon={ExternalLink}
                    label="Open on GitHub"
                    disabled={!repoFull}
                    onClick={openOnGitHub}
                  />
                  <HeaderMenuItem icon={Link2} label="Copy session ID" onClick={copySessionId} />
                </div>
              )}
              <HeaderMenuItem icon={Pencil} label="Rename" onClick={renameSession} />
              {/* The header density toggle is hidden on narrow screens, so this
                  submenu is the only way to switch transcript view there. */}
              <HeaderMenuItem
                icon={FileText}
                label="Transcript view"
                sub
                onClick={() => setSub((s) => (s === "view" ? null : "view"))}
              />
              {sub === "view" && (
                <div className="ml-3 border-l border-border/40 pl-1">
                  {(["normal", "verbose", "summary"] as const).map((m) => (
                    <HeaderMenuItem
                      key={m}
                      icon={FileText}
                      label={`${m[0].toUpperCase()}${m.slice(1)}`}
                      active={viewMode === m}
                      onClick={() => {
                        setView(m);
                        setMenuOpen(false);
                      }}
                    />
                  ))}
                </div>
              )}
              <HeaderMenuItem icon={Link2} label="Copy link" onClick={copyLink} />
              <HeaderMenuItem icon={Settings2} label="Edit environment" onClick={editEnvironment} />
              <div className="my-1 border-t border-border/50" />
              <HeaderMenuItem icon={Archive} label="Archive" onClick={archiveSession} />
              <HeaderMenuItem icon={Trash2} label="Delete" danger onClick={deleteSession} />
            </div>
          )}
        </div>
        <div className="relative flex items-center gap-1" ref={layoutRef}>
          {/* Transcript density toggle (Normal / Verbose / Summary). */}
          <div className="mr-1 hidden items-center rounded-md bg-accent/30 p-0.5 text-[10.5px] sm:flex">
            {(["normal", "verbose", "summary"] as const).map((m) => (
              <button
                key={m}
                type="button"
                title={`${m[0].toUpperCase()}${m.slice(1)} view`}
                onClick={() => setView(m)}
                className={`rounded px-1.5 py-0.5 capitalize ${viewMode === m ? "bg-card text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground"}`}
              >
                {m === "normal" ? "Normal" : m === "verbose" ? "Verbose" : "Summary"}
              </button>
            ))}
          </div>
          {diffStat && (
            <button
              type="button"
              onClick={() => onTogglePanel("diff")}
              title="View changes"
              className="mr-1 inline-flex items-center gap-1.5 rounded-md px-1.5 py-1 text-[11.5px] hover:bg-accent/50"
            >
              <span className="text-emerald-500">+{diffStat.adds}</span>
              <span className="text-red-500">−{diffStat.dels}</span>
            </button>
          )}
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
          {foldEvents(dedupeEvents(events))
            .filter((item) =>
              // Summary = prose only: drop the init block + tool/status events.
              viewMode !== "summary"
                ? true
                : item.kind === "event" &&
                  ["user_prompt", "user", "assistant", "result"].includes(item.event.type),
            )
            .map((item) => {
            // Container init / status run → one collapsible "Initialized
            // session" block (claude.ai-style), instead of N bare rows.
            if (item.kind === "init") {
              const failed = item.steps.some((s) => s.text.startsWith("✗"));
              // Light inline line (no bordered box), matching the claude.ai
              // loading style: "Initialized session ›".
              return (
                <div key={item.key}>
                  <button
                    type="button"
                    onClick={() => setInitOpen((o) => !o)}
                    className="flex items-center gap-1 text-left text-[13px] text-muted-foreground hover:text-foreground"
                  >
                    <span className="font-medium text-foreground/80">{failed ? "Session failed to start" : "Initialized session"}</span>
                    {initOpen || viewMode === "verbose" ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
                  </button>
                  {(initOpen || viewMode === "verbose") && (
                    <div className="mt-1 space-y-1 border-l border-border/40 pl-3">
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
              // Command output (e.g. the "Set model to …" confirmation) is not
              // a user message — show it as a quiet centered system note.
              const cmd = commandOutput(prompt);
              if (cmd) {
                return (
                  <div key={e.cursor} className="text-center text-[12px] text-muted-foreground/70">
                    {cmd}
                  </div>
                );
              }
              return (
                <div key={e.cursor} className="flex justify-end">
                  <div className="max-w-[80%] whitespace-pre-wrap break-words rounded-2xl bg-accent/40 px-3.5 py-1.5 text-[13px] text-foreground">
                    {stripTags(prompt)}
                  </div>
                </div>
              );
            }
            // Assistant turn: render text + tool calls in order (claude.ai/code
            // style) — a file Write shows its path + code, Bash its command +
            // output — instead of the old flattened "⚙ Write".
            if (e.type === "assistant") {
              const items = assistantItems(e.payload);
              if (items.length === 0) return null;
              return (
                <div key={e.cursor} className="flex flex-col gap-2">
                  {items.map((it, i) =>
                    it.kind === "tool" ? (
                      <ToolCall
                        key={i}
                        use={it.use}
                        result={toolResults.get(it.use.id)}
                        viewMode={viewMode}
                      />
                    ) : (
                      <div key={i} className="group/msg flex flex-col">
                        <div className="flex gap-2.5">
                          <span className="mt-1.5 size-1.5 shrink-0 rounded-full bg-amber-500" />
                          <div className="min-w-0 flex-1 text-[13px] text-foreground/90">
                            <MessageBoundary
                              fallback={
                                <pre className="whitespace-pre-wrap break-words font-sans">
                                  {stripTags(it.text)}
                                </pre>
                              }
                            >
                              <Markdown content={stripTags(it.text)} />
                            </MessageBoundary>
                          </div>
                        </div>
                        <div className="opacity-0 transition-opacity group-hover/msg:opacity-100 focus-within:opacity-100">
                          <MessageActions
                            text={stripTags(it.text)}
                            ts={e.created_at}
                            uuid={str(e.payload, "uuid")}
                            sessionId={sessionId}
                            pinnedUuids={pinnedUuids}
                          />
                        </div>
                      </div>
                    ),
                  )}
                </div>
              );
            }
            // `result` is turn-completion metadata; its `result` field just
            // echoes the final assistant message (already rendered above), so
            // rendering it produced a DUPLICATE reply. Skip it.
            if (e.type === "result") return null;
            const rawText = str(e.payload, "text") ?? str(e.payload, "content") ?? str(e.payload, "message") ?? sdkText(e.payload);
            const text = rawText ? stripTags(rawText) : "";
            // Empty content-less message (e.g. an `assistant` shell carrying
            // only tool calls already shown) — don't render a bare "assistant".
            if (!text) return null;
            // Tool-use indicator (⚙ Read, ⚙ Bash…) → quiet line, not prose.
            const isTool = text.startsWith("⚙");
            return (
              <div key={e.cursor} className="group/msg flex flex-col">
                <div className="flex gap-2.5">
                  <span className="mt-1.5 size-1.5 shrink-0 rounded-full bg-amber-500" />
                  {isTool ? (
                    <div className="min-w-0 text-[13px] text-muted-foreground">{text}</div>
                  ) : (
                    <div className="min-w-0 flex-1 text-[13px] text-foreground/90">
                      <MessageBoundary fallback={<pre className="whitespace-pre-wrap break-words font-sans">{text}</pre>}>
                        <Markdown content={text} />
                      </MessageBoundary>
                    </div>
                  )}
                </div>
                {/* Action bar — only for real prose replies (not tool lines). */}
                {!isTool && (
                  <div className="opacity-0 transition-opacity group-hover/msg:opacity-100 focus-within:opacity-100">
                    <MessageActions text={text} ts={e.created_at} uuid={str(e.payload, "uuid")} sessionId={sessionId} pinnedUuids={pinnedUuids} />
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
              <div className="min-w-0 flex-1 text-[13px] text-foreground/80">
                <MessageBoundary fallback={<pre className="whitespace-pre-wrap break-words font-sans">{stripTags(live)}</pre>}>
                  <Markdown content={stripTags(live)} isStreaming />
                </MessageBoundary>
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

      {/* Stop lives in the composer send button (claude-style — it becomes a
          stop square while the agent runs). This bar only surfaces send/
          permission errors. */}
      {sendError && (
        <div className="border-t border-border/60 px-6 py-2">
          <p className="mx-auto max-w-3xl text-[12px] text-red-500">{sendError}</p>
        </div>
      )}
    </div>
  );
}
