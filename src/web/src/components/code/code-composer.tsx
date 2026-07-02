"use client";

import { useRef, useEffect, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import Link from "next/link";
import {
  Cloud,
  Monitor,
  Code2,
  GitBranch,
  Plus,
  CornerDownLeft,
  ChevronDown,
  Mic,
  Gauge,
  Square,
  Loader2,
  Check,
  ExternalLink,
  Settings,
  Search,
  Paperclip,
  CircleDot,
  SquareSlash,
  Blocks,
  X,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Switch } from "@/components/ui/switch";
import { MODELS_META, PROVIDER_LABEL, type Provider } from "@/lib/ai/models-meta";
import { ConnectorsModal, ImportIssueModal } from "./code-connectors";
import { shouldSubmitOnEnter, useAutoResize } from "@/lib/chat/enter-submit";

const TOOLBAR_ICON_BTN =
  "flex size-6 items-center justify-center rounded text-foreground/50 hover:bg-accent/40 hover:text-foreground transition-colors";

// Minimal SpeechRecognition typings — not in this project's TS DOM lib, and
// `webkitSpeechRecognition` is vendor-prefixed. (SpeechRecognitionResultList
// IS in the lib, so we reuse it.)
interface SpeechRecognitionEvent extends Event {
  resultIndex: number;
  results: SpeechRecognitionResultList;
}
interface SpeechRecognition extends EventTarget {
  lang: string;
  interimResults: boolean;
  continuous: boolean;
  onresult: ((e: SpeechRecognitionEvent) => void) | null;
  onend: (() => void) | null;
  onerror: (() => void) | null;
  start(): void;
  stop(): void;
  abort(): void;
}
declare global {
  interface Window {
    SpeechRecognition?: { new (): SpeechRecognition };
    webkitSpeechRecognition?: { new (): SpeechRecognition };
  }
}

type Machine = {
  environment_id: string;
  machine_name: string;
  directory: string;
  branch: string | null;
  git_repo_url: string | null;
  worker_type: string;
  last_seen_at: number;
  online: boolean;
};

/** A pending image attachment (base64, no data: prefix) for the next send. */
export type Attachment = {
  name: string;
  media_type: string;
  /** base64 of the image bytes, no `data:...;base64,` prefix (images only). */
  data: string;
  /** data URL for the inline thumbnail (images only). */
  preview: string;
  /** "image" → vision content block; "file" → text inlined into the prompt. */
  kind?: "image" | "file";
  /** Decoded text content (files only). */
  text?: string;
};

const MAX_ATTACH_BYTES = 5 * 1024 * 1024; // 5MB/image — keep request bodies sane

type Popover = null | "env" | "repo" | "addrepo" | "plus" | "plus2" | "model" | "effort" | "mode" | "mic" | "usage" | "conn";

// Slash-command palette (claude.ai/code parity). "web" commands are handled by
// the page (onCommand) or open a composer popover (/model); "send" commands are
// dispatched as the message text — the worker IS Claude Code and runs them
// natively (it accepts bridge-safe slash commands over the inbound).
type SlashCommand = { name: string; desc: string; kind: "web" | "send" };
// Verified live against the container worker (2026-06-13): only commands that
// produce a useful response over the bridge are listed. Dropped: /status,
// /skills, /memory (interactive Ink UI — no web output), /pr-comments (no
// response), /rename (not a real command over the bridge).
const SLASH_COMMANDS: SlashCommand[] = [
  { name: "clear", desc: "Start a new session", kind: "web" },
  { name: "init", desc: "Analyze the repo and write an AGENTS.md", kind: "send" },
  { name: "review", desc: "Review the current changes", kind: "send" },
  { name: "commit", desc: "Stage and commit the changes", kind: "send" },
  { name: "compact", desc: "Compact the conversation to free up context", kind: "send" },
  { name: "diff", desc: "Show the changes (Diff panel)", kind: "web" },
  { name: "model", desc: "Switch the model", kind: "web" },
  { name: "cost", desc: "Token usage and cost for this session", kind: "send" },
  { name: "context", desc: "Context-window usage breakdown", kind: "send" },
  { name: "security-review", desc: "Security review of the changes", kind: "send" },
  { name: "help", desc: "List keyboard shortcuts & commands", kind: "web" },
];

// Permission modes, claude.ai/code naming. Values are the CLI's
// ExternalPermissionMode strings, applied via set_permission_mode
// control_requests (live sessions) or seeded at task dispatch.
const MODE_OPTIONS: { label: string; value: string; n: string }[] = [
  { label: "Accept edits", value: "acceptEdits", n: "1" },
  { label: "Plan mode", value: "plan", n: "2" },
  { label: "Auto mode", value: "bypassPermissions", n: "3" },
];

// Real model catalog grouped by provider (from the browser-safe MODELS_META —
// Anthropic / DeepSeek / Google / Groq / Kimi / OpenAI), in this display order.
const MODEL_PROVIDER_ORDER: Provider[] = [
  "anthropic",
  "deepseek",
  "google",
  "kimi",
  "openai",
];
const MODEL_GROUPS = MODEL_PROVIDER_ORDER.map((provider) => ({
  provider,
  label: PROVIDER_LABEL[provider],
  models: Object.values(MODELS_META).filter((m) => m.provider === provider),
})).filter((g) => g.models.length > 0);

const PLUS_ITEMS: { icon: LucideIcon; label: string; chord: string; sub?: boolean }[] = [
  { icon: Paperclip, label: "Add files or photos", chord: "Ctrl+U" },
  { icon: CircleDot, label: "Import GitHub issue", chord: "" },
  { icon: SquareSlash, label: "Slash commands", chord: "" },
  { icon: Blocks, label: "Connectors", chord: "", sub: true },
];

function repoLabel(m: Machine | null): string {
  if (!m) return "repo";
  if (m.git_repo_url) {
    const s = m.git_repo_url.replace(/\.git$/, "").split("/");
    return s.slice(-2).join("/") || s.slice(-1)[0];
  }
  return m.directory.split("/").filter(Boolean).slice(-1)[0] ?? "repo";
}

export function CodeComposer({
  value,
  onChange,
  onSubmit,
  busy = false,
  machines,
  selected,
  onPickMachine,
  onRefreshMachines,
  onConfigureEnvironment,
  onAddCloudEnvironment,
  placeholder = "Describe a task or ask a question",
  showPills = true,
  mode = "acceptEdits",
  onModeChange,
  onPickRepo,
  extraRepos = [],
  onExtraReposChange,
  attachments = [],
  onAttachmentsChange,
  model = "claude-sonnet-4-6",
  onModelChange,
  connectors = [],
  onConnectorsChange,
  availableConnectors = [],
  connectorsEditable = true,
  running = false,
  onStop,
  onCommand,
}: {
  value: string;
  onChange: (v: string) => void;
  onSubmit: () => void;
  busy?: boolean;
  machines: Machine[] | null;
  selected: Machine | null;
  onPickMachine: (m: Machine) => void;
  onRefreshMachines: () => void;
  /** Open the "Update cloud environment" config for a cloud env (the gear next
   *  to its checkmark in the env picker). */
  onConfigureEnvironment?: (envId: string) => void;
  /** "Add cloud environment…" — open the "New cloud environment" create modal. */
  onAddCloudEnvironment?: () => void;
  placeholder?: string;
  showPills?: boolean;
  mode?: string;
  onModeChange?: (mode: string) => void;
  /** Picking a GitHub repo targets a cloud container for the next task. */
  onPickRepo?: (fullName: string | null) => void;
  /** Additional repos (multi-repo session) shown as pills with a remove ×; the
   *  "+" in the pill row adds one via the repo picker (claude.ai/code parity). */
  extraRepos?: string[];
  onExtraReposChange?: (repos: string[]) => void;
  /** Pending image attachments for the next send (owned by the page). */
  attachments?: Attachment[];
  onAttachmentsChange?: (a: Attachment[]) => void;
  /** Selected model id (MODELS_META key); applied to the session/task. */
  model?: string;
  onModelChange?: (id: string) => void;
  /** Per-session MCP connectors opted in for the next task (server ids). Empty
   *  by default — nothing attaches unless the user picks it. */
  connectors?: string[];
  onConnectorsChange?: (ids: string[]) => void;
  /** Enabled, container-capable connectors the picker offers. */
  availableConnectors?: { id: string; name: string }[];
  /** False on an already-open session — connectors are baked in at launch, so
   *  the picker only shows for a new session. */
  connectorsEditable?: boolean;
  /** True while the open session's agent is running → send becomes Stop. */
  running?: boolean;
  onStop?: () => void;
  /** Web-action slash commands (/clear, /diff, /help) handled by the page; the
   *  rest are sent to the worker, which runs them natively. */
  onCommand?: (name: string) => void;
}) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState<Popover>(null);
  const [attachErr, setAttachErr] = useState<string | null>(null);

  // ── Slash-command palette (type "/" → menu) ────────────────────────────
  const [slashIndex, setSlashIndex] = useState(0);
  const [slashDismissed, setSlashDismissed] = useState(false);
  // Open while typing a bare "/command" (no space yet); a space → args mode.
  const slashQuery = /^\/(\S*)$/.exec(value)?.[1]?.toLowerCase() ?? null;
  const slashResults =
    slashQuery === null ? [] : SLASH_COMMANDS.filter((c) => c.name.includes(slashQuery));
  const slashOpen = slashQuery !== null && !slashDismissed && slashResults.length > 0;
  const slashActive = Math.min(slashIndex, Math.max(0, slashResults.length - 1));

  const handleInputChange = (v: string) => {
    setSlashDismissed(false);
    setSlashIndex(0);
    onChange(v);
  };
  const runSlash = (cmd: SlashCommand) => {
    if (cmd.kind === "web") {
      if (cmd.name === "model") setOpen("model");
      else onCommand?.(cmd.name);
      onChange("");
    } else {
      // Fill "/name " (trailing space closes the palette → args mode); the
      // user hits Enter and the normal submit path delivers it to the worker.
      onChange("/" + cmd.name + " ");
      textareaRef.current?.focus();
    }
  };
  const handleKeyDown = (e: ReactKeyboardEvent<HTMLTextAreaElement>) => {
    if (slashOpen) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setSlashIndex((i) => Math.min(i + 1, slashResults.length - 1));
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setSlashIndex((i) => Math.max(i - 1, 0));
        return;
      }
      if (e.key === "Enter" || e.key === "Tab") {
        e.preventDefault();
        runSlash(slashResults[slashActive]);
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        setSlashDismissed(true);
        return;
      }
    }
    // IME-safe: don't submit while composing a CJK candidate / dead-key
    // accent — otherwise Enter-to-confirm sends a half-written message.
    if (shouldSubmitOnEnter(e)) {
      e.preventDefault();
      if (value.trim() || attachments.length) onSubmit();
    }
  };

  // Read picked files. Images → base64 (a vision model sees them as content
  // blocks); any other file → decoded text inlined into the prompt (handy for
  // logs, configs, snippets). Binary non-images come through as text and may
  // be garbled — pick text files.
  const onFilesPicked = (files: FileList | null) => {
    if (!files || !onAttachmentsChange) return;
    setAttachErr(null);
    const picked = Array.from(files);
    Promise.all(
      picked.map(
        (f) =>
          new Promise<Attachment | null>((resolve) => {
            if (f.size > MAX_ATTACH_BYTES) {
              setAttachErr(`${f.name} is over 5MB.`);
              resolve(null);
              return;
            }
            const reader = new FileReader();
            if (!f.type.startsWith("image/")) {
              // Non-image → inline as text.
              reader.onload = () =>
                resolve({
                  name: f.name,
                  media_type: f.type || "text/plain",
                  data: "",
                  preview: "",
                  kind: "file",
                  text: String(reader.result),
                });
              reader.onerror = () => resolve(null);
              reader.readAsText(f);
              return;
            }
            reader.onload = () => {
              const url = String(reader.result);
              resolve({ name: f.name, media_type: f.type, data: url.split(",")[1] ?? "", preview: url, kind: "image" });
            };
            reader.onerror = () => resolve(null);
            reader.readAsDataURL(f);
          }),
      ),
    ).then((results) => {
      const added = results.filter((a): a is Attachment => !!a);
      if (added.length) onAttachmentsChange([...attachments, ...added]);
    });
  };
  const [repoQuery, setRepoQuery] = useState("");
  const [modal, setModal] = useState<null | "connectors" | "import">(null);
  const [ghRepos, setGhRepos] = useState<{ full_name: string }[] | null>(null);
  const [repoOverride, setRepoOverride] = useState<string | null>(null);

  // Voice dictation via the browser SpeechRecognition API (Chrome/Edge) —
  // client-side, no backend. Transcribed text is appended to the composer.
  const [holdToRecord, setHoldToRecord] = useState(true);
  const [recording, setRecording] = useState(false);
  const recRef = useRef<SpeechRecognition | null>(null);
  // First mic use requests permission via getUserMedia (Chrome's device-aware
  // prompt — claude.ai parity). micReadyRef avoids re-requesting; stopRequestedRef
  // guards a hold-to-record release that lands while the prompt is still open.
  const micReadyRef = useRef(false);
  const stopRequestedRef = useRef(false);
  const valueRef = useRef(value);
  useEffect(() => {
    valueRef.current = value;
  }, [value]);
  // Detect AFTER mount: reading `window` during render makes SSR (false) and
  // the client (true) disagree → hydration mismatch. Both render "unsupported"
  // first, then the client enables the mic post-mount.
  const [speechSupported, setSpeechSupported] = useState(false);
  useEffect(() => {
    setSpeechSupported(!!(window.SpeechRecognition || window.webkitSpeechRecognition));
  }, []);

  const startRec = async () => {
    if (!speechSupported || recording) return;
    stopRequestedRef.current = false;
    setRecording(true); // reflect intent immediately (the prompt may take a beat)
    // Request the mic via getUserMedia FIRST so Chrome shows its full
    // device-aware permission prompt (parity with claude.ai's "Use available
    // microphones") and grants a persistent permission, then release the stream
    // — SpeechRecognition handles the actual audio. The Web Speech API can't
    // target a specific input device, so true per-device selection still needs a
    // server-STT switch (docs/decisions-pending.md #17).
    if (!micReadyRef.current && navigator.mediaDevices?.getUserMedia) {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        stream.getTracks().forEach((t) => t.stop());
        micReadyRef.current = true;
      } catch {
        setRecording(false);
        return;
      }
    }
    if (stopRequestedRef.current) { setRecording(false); return; } // released during prompt
    const Ctor = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!Ctor) { setRecording(false); return; }
    const rec = new Ctor();
    rec.lang = navigator.language || "en-US";
    rec.interimResults = false;
    rec.continuous = holdToRecord; // hold = keep listening until release
    rec.onresult = (ev: SpeechRecognitionEvent) => {
      let finalText = "";
      for (let i = ev.resultIndex; i < ev.results.length; i++) {
        if (ev.results[i].isFinal) finalText += ev.results[i][0].transcript;
      }
      if (finalText.trim()) {
        const base = valueRef.current;
        onChange((base ? base.replace(/\s*$/, "") + " " : "") + finalText.trim());
      }
    };
    rec.onend = () => setRecording(false);
    rec.onerror = () => setRecording(false);
    recRef.current = rec;
    try {
      rec.start();
    } catch {
      setRecording(false);
    }
  };
  const stopRec = () => {
    stopRequestedRef.current = true;
    recRef.current?.stop();
  };
  const toggleRec = () => (recording ? stopRec() : startRec());
  useEffect(() => () => recRef.current?.abort(), []);

  // Auto-grow up to 240px, then scroll internally — shared with the chat
  // composer (enter-submit.ts). Previously this had NO cap, so a long
  // paste pushed the toolbar + send button off-screen.
  useAutoResize(textareaRef, value, 240);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(null);
    };
    window.addEventListener("mousedown", onDown);
    return () => window.removeEventListener("mousedown", onDown);
  }, [open]);

  // Pull the user's GitHub repos (when GitHub is connected) for the repo picker.
  useEffect(() => {
    fetch("/api/github/repos")
      .then((r) => (r.ok ? r.json() : null))
      .then((d: { ok?: boolean; repos?: { full_name: string }[] } | null) => {
        if (d?.ok && d.repos) setGhRepos(d.repos);
      })
      .catch(() => {});
  }, []);

  const pill =
    "flex items-center gap-1.5 rounded-full border border-border/60 bg-accent/30 px-2.5 py-1 text-[12px] text-foreground/70 hover:bg-accent/50 hover:text-foreground transition-colors";
  const toggle = (p: Popover) => setOpen((cur) => (cur === p ? null : p));
  const ghFiltered = (ghRepos ?? []).filter((r) =>
    r.full_name.toLowerCase().includes(repoQuery.toLowerCase()),
  );

  // Shared plus-menu items — rendered from both the pills-row "+" (welcome
  // view) and the always-visible bottom-toolbar "+" (next to the mic).
  const onPlusItem = (label: string) => {
    if (label === "Connectors") setModal("connectors");
    else if (label === "Import GitHub issue") setModal("import");
    else if (label === "Add files or photos") fileInputRef.current?.click();
    else if (label === "Slash commands") {
      if (!value.startsWith("/")) onChange("/" + value);
      textareaRef.current?.focus();
    }
    setOpen(null);
  };
  const plusItems = PLUS_ITEMS.map((it) => (
    <button
      key={it.label}
      type="button"
      onClick={() => onPlusItem(it.label)}
      className="flex w-full items-center gap-2.5 rounded px-2.5 py-1.5 text-left text-[13px] text-foreground/90 hover:bg-accent/50"
    >
      <it.icon className="size-3.5 text-muted-foreground" />
      <span className="flex-1">{it.label}</span>
      {it.sub && <ChevronDown className="size-3.5 -rotate-90 text-muted-foreground" />}
      {it.chord && <span className="text-[11px] text-muted-foreground/60">{it.chord}</span>}
    </button>
  ));

  return (
    <div className="border border-border/60 rounded-2xl overflow-visible bg-card" ref={rootRef}>
      {/* pills (welcome view only) */}
      {showPills && (
        <div className="relative flex items-center gap-1.5 px-3 py-2 border-b border-border/40">
          {/* environment */}
          <button type="button" onClick={() => toggle("env")} className={pill}>
            {selected && selected.worker_type !== "container" ? <Monitor className="size-3 text-foreground/60" /> : <Cloud className="size-3 text-foreground/60" />}
            {selected?.machine_name ?? "Default"}
            <ChevronDown className="size-3 opacity-50" />
          </button>
          {open === "env" && (
            <div className="absolute bottom-full left-0 mb-2 w-[320px] rounded-xl border border-border bg-card p-1.5 shadow-xl z-50">
              {/* Local — desktop-only placeholder. The web can't run the local
                  env directly; use Cloud or a Remote-Control machine. */}
              <div className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5" title="The local environment is only available in the desktop app">
                <Monitor className="size-3.5 shrink-0 text-muted-foreground" />
                <span className="text-[13px] text-muted-foreground">Local</span>
                <span className="text-[11px] text-muted-foreground/60">Desktop only</span>
              </div>

              {/* Cloud — configurable container environments (gear → Update). */}
              <div className="mt-1 border-t border-border/40 px-2 pb-1 pt-2 text-[11px] font-medium text-foreground/60">Cloud</div>
              {machines === null ? (
                <div className="flex items-center gap-2 px-2 py-1.5 text-[12px] text-muted-foreground"><Loader2 className="size-3.5 animate-spin" /> Loading…</div>
              ) : (
                machines
                  .filter((m) => m.worker_type === "container")
                  .map((m) => (
                    <MachineRow
                      key={m.environment_id}
                      m={m}
                      selected={selected}
                      onPick={() => { onPickMachine(m); onPickRepo?.(null); setRepoOverride(null); setOpen(null); }}
                      onConfigure={onConfigureEnvironment ? (id) => { onConfigureEnvironment(id); setOpen(null); } : undefined}
                    />
                  ))
              )}
              <button
                type="button"
                onClick={() => { onAddCloudEnvironment?.(); setOpen(null); }}
                className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-[13px] text-foreground/80 hover:bg-accent/40"
              >
                <Plus className="size-3.5 text-muted-foreground" /> Add cloud environment…
              </button>

              {/* Remote Control — claude.ai shows only the setup prompt here (no
                  inline machine list). Connected machines' sessions live in the
                  left sidebar, so the picker stays a clean match. */}
              <div className="mt-1 border-t border-border/40 px-2 pb-1 pt-2 text-[11px] font-medium text-foreground/60">Remote Control</div>
              <div className="px-2 py-1.5">
                <div className="pl-[22px] text-[13px] text-foreground/90">Set up Remote Control</div>
                <div className="mt-0.5 flex items-start gap-2">
                  <ExternalLink className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
                  <div className="text-[11px] text-muted-foreground">Run <code className="text-[10.5px]">/remote-control</code> on your machine to code from here.</div>
                </div>
              </div>
            </div>
          )}

          {/* repo */}
          <button type="button" onClick={() => toggle("repo")} className={pill}>
            <Code2 className="size-3 text-foreground/60" />
            {repoOverride ?? repoLabel(selected)}
          </button>
          {open === "repo" && (
            <div className="absolute bottom-full left-20 mb-2 w-[320px] rounded-xl border border-border bg-card p-1.5 shadow-xl z-50">
              {/* GitHub repos ONLY — the machine the task runs on is the env
                  pill's job; this picks WHAT to work on. */}
              <div className="max-h-[300px] overflow-y-auto">
                {ghRepos === null ? (
                  <div className="px-2 py-2 text-[12px] text-muted-foreground">
                    Connect GitHub (＋ → Connectors) to pick a repository.
                  </div>
                ) : ghFiltered.length === 0 ? (
                  <div className="px-2 py-1.5 text-[12px] text-muted-foreground">
                    {repoQuery ? "No matching repositories." : "No repositories found."}
                  </div>
                ) : (
                  ghFiltered.slice(0, 60).map((r) => (
                    <button key={r.full_name} type="button" onClick={() => { setRepoOverride(r.full_name); onPickRepo?.(r.full_name); setOpen(null); }} className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-[13px] hover:bg-accent/40">
                      <Code2 className="size-3.5 shrink-0 text-muted-foreground" />
                      <span className="flex-1 truncate text-foreground/90">{r.full_name}</span>
                      {repoOverride === r.full_name && <Check className="size-3.5 text-primary" />}
                    </button>
                  ))
                )}
              </div>
              <div className="mt-1 flex items-center gap-1.5 rounded-lg border border-border/50 bg-accent/20 px-2 py-1.5">
                <Search className="size-3.5 text-muted-foreground" />
                <input value={repoQuery} onChange={(e) => setRepoQuery(e.target.value)} placeholder="Search repos…" className="flex-1 bg-transparent text-[12.5px] text-foreground placeholder:text-muted-foreground/50 focus:outline-none" />
              </div>
            </div>
          )}

          {/* branch */}
          <button type="button" className={pill}>
            <GitBranch className="size-3 text-foreground/60" />
            {selected?.branch ?? "main"}
          </button>

          {/* extra repos (multi-repo) — each a pill with its branch + remove × */}
          {extraRepos.map((r) => (
            <span key={r} className={pill}>
              <Code2 className="size-3 text-foreground/60" />
              {r.split("/").pop()}
              <GitBranch className="ml-1 size-3 text-foreground/60" />
              main
              <button
                type="button"
                aria-label={`Remove ${r}`}
                onClick={() => onExtraReposChange?.(extraRepos.filter((x) => x !== r))}
                className="ml-0.5 -mr-0.5 text-muted-foreground hover:text-foreground"
              >
                <X className="size-3" />
              </button>
            </span>
          ))}

          {/* + → add another repo via the repo picker (claude.ai/code parity) */}
          <button type="button" aria-label="Add repository" onClick={() => toggle("addrepo")} className={`${pill} px-1.5`}>
            <Plus className="size-3" />
          </button>
          {open === "addrepo" && (
            <div className="absolute bottom-full left-32 mb-2 w-[320px] rounded-xl border border-border bg-card p-1.5 shadow-xl z-50">
              <div className="max-h-[300px] overflow-y-auto">
                {ghRepos === null ? (
                  <div className="px-2 py-2 text-[12px] text-muted-foreground">Connect GitHub (＋ → Connectors) to pick a repository.</div>
                ) : ghFiltered.length === 0 ? (
                  <div className="px-2 py-1.5 text-[12px] text-muted-foreground">{repoQuery ? "No matching repositories." : "No repositories found."}</div>
                ) : (
                  ghFiltered.slice(0, 60).map((r) => {
                    const added = extraRepos.includes(r.full_name) || repoOverride === r.full_name;
                    return (
                      <button
                        key={r.full_name}
                        type="button"
                        onClick={() => { if (!added) onExtraReposChange?.([...extraRepos, r.full_name]); setOpen(null); }}
                        className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-[13px] hover:bg-accent/40"
                      >
                        <Code2 className="size-3.5 shrink-0 text-muted-foreground" />
                        <span className="flex-1 truncate text-foreground/90">{r.full_name}</span>
                        {added && <Check className="size-3.5 text-primary" />}
                      </button>
                    );
                  })
                )}
              </div>
              <div className="mt-1 flex items-center gap-1.5 rounded-lg border border-border/50 bg-accent/20 px-2 py-1.5">
                <Search className="size-3.5 text-muted-foreground" />
                <input value={repoQuery} onChange={(e) => setRepoQuery(e.target.value)} placeholder="Search repos…" className="flex-1 bg-transparent text-[12.5px] text-foreground placeholder:text-muted-foreground/50 focus:outline-none" />
              </div>
            </div>
          )}
        </div>
      )}

      {/* hidden file picker for "Add files or photos" */}
      <input
        ref={fileInputRef}
        type="file"
        multiple
        className="hidden"
        onChange={(e) => {
          onFilesPicked(e.target.files);
          e.target.value = ""; // allow re-picking the same file
        }}
      />

      {/* attachment thumbnails (above the input) */}
      {attachments.length > 0 && (
        <div className="flex flex-wrap gap-2 px-3 pt-2">
          {attachments.map((a, i) => (
            <div key={`${a.name}-${i}`} className="group relative">
              {a.kind === "file" ? (
                <div
                  title={a.name}
                  className="flex size-12 flex-col items-center justify-center rounded-md border border-border/60 bg-accent/30 px-1 text-center"
                >
                  <Paperclip className="size-4 text-muted-foreground" />
                  <span className="mt-0.5 w-full truncate text-[8px] text-muted-foreground">{a.name}</span>
                </div>
              ) : (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={a.preview} alt={a.name} className="size-12 rounded-md border border-border/60 object-cover" />
              )}
              <button
                type="button"
                aria-label={`Remove ${a.name}`}
                onClick={() => onAttachmentsChange?.(attachments.filter((_, j) => j !== i))}
                className="absolute -right-1.5 -top-1.5 flex size-4 items-center justify-center rounded-full bg-card text-muted-foreground shadow ring-1 ring-border hover:text-foreground"
              >
                <X className="size-2.5" />
              </button>
            </div>
          ))}
        </div>
      )}
      {attachErr && <div className="px-3 pt-1 text-[12px] text-red-500">{attachErr}</div>}

      {/* input + send */}
      <div className="relative flex items-center gap-2 px-3 py-2 border-b border-border/40">
        {slashOpen && (
          <div className="absolute bottom-full left-2 right-2 mb-2 max-h-[280px] overflow-y-auto rounded-xl border border-border bg-card p-1 shadow-xl z-50">
            {slashResults.map((c, i) => (
              <button
                key={c.name}
                type="button"
                onMouseEnter={() => setSlashIndex(i)}
                // mousedown (not click) + preventDefault keeps textarea focus
                // so runSlash's focus()/value change isn't fighting a blur.
                onMouseDown={(e) => { e.preventDefault(); runSlash(c); }}
                className={`flex w-full items-center gap-2 rounded px-2.5 py-1.5 text-left ${i === slashActive ? "bg-accent/60" : "hover:bg-accent/40"}`}
              >
                <span className="shrink-0 font-mono text-[13px] text-foreground">/{c.name}</span>
                <span className="truncate text-[12px] text-muted-foreground">{c.desc}</span>
              </button>
            ))}
          </div>
        )}
        <textarea
          ref={textareaRef}
          value={value}
          onChange={(e) => handleInputChange(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={placeholder}
          rows={1}
          className="flex-1 resize-none bg-transparent text-[13px] text-foreground placeholder:text-muted-foreground/50 focus:outline-none"
        />
        {running ? (
          <button type="button" onClick={onStop} aria-label="Stop" title="Stop" className="flex size-7 shrink-0 items-center justify-center rounded-md bg-foreground/90 text-background hover:bg-foreground transition-colors">
            <Square className="size-3 fill-current" />
          </button>
        ) : (
          <button type="button" onClick={onSubmit} disabled={busy || (!value.trim() && attachments.length === 0)} aria-label="Send" className="flex size-7 shrink-0 items-center justify-center rounded-md bg-primary text-primary-foreground hover:bg-primary/90 transition-colors disabled:opacity-40 disabled:pointer-events-none">
            {busy ? <Loader2 className="size-3.5 animate-spin" /> : <CornerDownLeft className="size-3.5" />}
          </button>
        )}
      </div>

      {/* toolbar */}
      <div className="relative flex items-center justify-between px-3 py-2">
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => toggle("mode")}
            className="rounded bg-accent/40 px-2 py-1 text-[12px] text-foreground/80 hover:bg-accent/60 hover:text-foreground"
          >
            {MODE_OPTIONS.find((m) => m.value === mode)?.label ?? "Accept edits"}
          </button>
          <button type="button" aria-label="Attach" onClick={() => toggle("plus2")} className={`${TOOLBAR_ICON_BTN} ${open === "plus2" ? "bg-accent/40 text-foreground" : ""}`}><Plus className="size-3.5" /></button>
          {open === "plus2" && (
            <div className="absolute bottom-full left-2 mb-2 w-[240px] rounded-xl border border-border bg-card p-1 shadow-xl z-50">
              {plusItems}
            </div>
          )}
          <button
            type="button"
            aria-label={recording ? "Stop recording" : "Record"}
            title={speechSupported ? (holdToRecord ? "Hold to record" : "Click to record") : "Voice input needs Chrome or Edge"}
            disabled={!speechSupported}
            {...(holdToRecord
              ? {
                  onMouseDown: startRec,
                  onMouseUp: stopRec,
                  onMouseLeave: () => recording && stopRec(),
                  onTouchStart: startRec,
                  onTouchEnd: stopRec,
                }
              : { onClick: toggleRec })}
            className={`${TOOLBAR_ICON_BTN} ${recording ? "!text-red-500" : ""} disabled:opacity-40`}
          >
            <Mic className={`size-3.5 ${recording ? "animate-pulse" : ""}`} />
          </button>
          <button type="button" aria-label="Microphone settings" onClick={() => toggle("mic")} className={TOOLBAR_ICON_BTN}>
            <ChevronDown className="size-3.5" />
          </button>
          {open === "mic" && (
            <div className="absolute bottom-full left-24 mb-2 w-[210px] rounded-xl border border-border bg-card p-2 shadow-xl z-50">
              <div className="px-1 pb-1.5 text-[11px] font-medium text-muted-foreground/60">Microphone</div>
              <label className="flex w-full cursor-pointer items-center gap-2 rounded px-1 py-1 text-[13px] text-foreground/90 hover:bg-accent/40">
                <span className="flex-1">Hold to record</span>
                <Switch checked={holdToRecord} onCheckedChange={setHoldToRecord} size="sm" />
              </label>
              {!speechSupported && (
                <p className="mt-1 px-1 text-[11px] text-muted-foreground/60">Voice input needs Chrome or Edge.</p>
              )}
            </div>
          )}
        </div>
        <div className="flex items-center gap-2 text-[11.5px] text-foreground/55">
          <button type="button" onClick={() => toggle("model")} className="rounded px-1.5 py-0.5 hover:bg-accent/40 hover:text-foreground">{MODELS_META[model]?.label ?? model}</button>
          <button type="button" onClick={() => toggle("effort")} className="rounded px-1.5 py-0.5 hover:bg-accent/40 hover:text-foreground">Max</button>
          <button type="button" aria-label="Usage" onClick={() => toggle("usage")} className="flex size-5 items-center justify-center rounded hover:bg-accent/40 hover:text-foreground">
            <Gauge className="size-3.5" />
          </button>
        </div>

        {open === "mode" && (
          <div
            className="absolute bottom-full left-2 mb-2 w-[210px] rounded-xl border border-border bg-card p-1 shadow-xl z-50"
            onKeyDown={(e) => {
              const hit = MODE_OPTIONS.find((m) => m.n === e.key);
              if (hit) {
                onModeChange?.(hit.value);
                setOpen(null);
              }
            }}
          >
            <div className="flex items-center justify-between px-2.5 py-1.5 text-[11px] text-muted-foreground/60">
              <span>Mode</span>
              <span className="font-mono">Ctrl Alt M</span>
            </div>
            {MODE_OPTIONS.map((m) => (
              <button
                key={m.value}
                type="button"
                onClick={() => {
                  onModeChange?.(m.value);
                  setOpen(null);
                }}
                className="flex w-full items-center gap-2 rounded px-2.5 py-1.5 text-left text-[13px] hover:bg-accent/50"
              >
                <span className="flex-1 text-foreground/90">{m.label}</span>
                <Check className={`size-3.5 ${m.value === mode ? "text-primary" : "opacity-0"}`} />
                <span className="text-[11px] text-muted-foreground/50">{m.n}</span>
              </button>
            ))}
          </div>
        )}

        {open === "model" && (
          <div className="absolute bottom-full right-2 mb-2 max-h-[400px] w-[280px] overflow-y-auto rounded-xl border border-border bg-card p-1 shadow-xl z-50">
            {MODEL_GROUPS.map((g) => (
              <div key={g.provider}>
                <div className="px-2.5 pb-1 pt-2 text-[11px] font-medium text-muted-foreground/60">{g.label}</div>
                {g.models.map((m) => (
                  <button
                    key={m.id}
                    type="button"
                    onClick={() => { onModelChange?.(m.id); setOpen(null); }}
                    className="flex w-full items-center gap-2 rounded px-2.5 py-1.5 text-left text-[13px] hover:bg-accent/50"
                  >
                    <Check className={`size-3.5 shrink-0 ${m.id === model ? "text-primary" : "opacity-0"}`} />
                    <span className="min-w-0 flex-1">
                      <span className="text-foreground/90">{m.label}</span>
                      {m.badge && <span className="ml-1 rounded bg-accent px-1 py-0.5 text-[9.5px] text-muted-foreground">{m.badge}</span>}
                      <span className="block truncate text-[11px] text-muted-foreground/60">{m.description}</span>
                    </span>
                  </button>
                ))}
              </div>
            ))}
          </div>
        )}

        {open === "effort" && (
          <div className="absolute bottom-full right-2 mb-2 w-[240px] rounded-xl border border-border bg-card p-3 shadow-xl z-50">
            <div className="mb-2 text-[12px] text-foreground/70">Effort <span className="font-medium text-foreground">Max</span></div>
            <div className="mb-1 flex justify-between text-[11px] text-muted-foreground/60"><span>Faster</span><span>Smarter</span></div>
            <input type="range" min={0} max={100} defaultValue={100} className="w-full accent-primary" />
          </div>
        )}

        {open === "usage" && (
          <div className="absolute bottom-full right-2 mb-2 w-[260px] rounded-xl border border-border bg-card p-3 shadow-xl z-50">
            <div className="flex items-center justify-between text-[13px]">
              <span className="font-medium text-foreground">Plan usage</span>
              <span className="text-[12px] text-emerald-500">Unlimited</span>
            </div>
            <p className="mt-1 text-[11.5px] leading-relaxed text-muted-foreground/80">
              Self-hosted — runs on your own API keys, so there&apos;s no Claude
              plan limit. Throughput is bounded by each provider&apos;s own rate
              limits.
            </p>
            <Link
              href="/settings?tab=usage"
              className="mt-2 inline-block text-[12px] text-blue-400 hover:underline"
            >
              Provider limits in Settings →
            </Link>
          </div>
        )}
      </div>

      {modal === "connectors" && <ConnectorsModal onClose={() => setModal(null)} />}
      {modal === "import" && <ImportIssueModal onClose={() => setModal(null)} onPick={(t) => onChange(t)} />}
    </div>
  );
}

/** One row in the machine/environment picker. Local machines show an online
 *  dot; cloud (container) environments show a config gear (→ Update cloud
 *  environment) next to the checkmark, matching claude.ai/code. */
function MachineRow({
  m,
  selected,
  onPick,
  onConfigure,
}: {
  m: Machine;
  selected: Machine | null;
  onPick: () => void;
  onConfigure?: (envId: string) => void;
}) {
  return (
    <div className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 hover:bg-accent/40">
      <button type="button" onClick={onPick} className="flex min-w-0 flex-1 items-center gap-2 text-left">
        {m.worker_type === "container" ? (
          <Cloud className="size-3.5 shrink-0 text-blue-400" />
        ) : (
          <span className="relative flex size-3.5 shrink-0 items-center justify-center" title={m.online ? "online" : "offline"}>
            <Monitor className="size-3.5 text-foreground/60" />
            <span className={`absolute -bottom-0.5 -right-0.5 size-1.5 rounded-full ring-1 ring-background ${m.online ? "bg-emerald-500" : "bg-muted-foreground/40"}`} />
          </span>
        )}
        <span className="flex-1 truncate text-[13px] text-foreground">{m.machine_name}</span>
        {m.worker_type === "claude_code_repl" && (
          <span className="shrink-0 rounded bg-accent/60 px-1 text-[10px] text-muted-foreground" title="An attached REPL session — can't run new tasks">attach-only</span>
        )}
      </button>
      {selected?.environment_id === m.environment_id && <Check className="size-3.5 shrink-0 text-primary" />}
      {m.worker_type === "container" && onConfigure && (
        <button
          type="button"
          aria-label="Configure environment"
          title="Configure environment"
          onClick={(e) => { e.stopPropagation(); onConfigure(m.environment_id); }}
          className="flex size-5 shrink-0 items-center justify-center rounded text-muted-foreground hover:bg-accent hover:text-foreground"
        >
          <Settings className="size-3.5" />
        </button>
      )}
    </div>
  );
}
