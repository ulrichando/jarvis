"use client";

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { Monitor, Square, CornerDownLeft, Hand, RotateCcw, RotateCw, Loader2, Plug, Unplug, ShieldCheck, Cpu, ChevronDown, Check } from "lucide-react";
import { motion, useReducedMotion } from "motion/react";
import { NoVNCView } from "@/components/computer-use/novnc-view";
import { Markdown } from "@/components/markdown/markdown";
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
} from "@/components/ui/dropdown-menu";

type Status = {
  ready: boolean;
  streamUp: boolean;
  sidecarUp: boolean;
  providers?: Record<string, boolean>;
  wsUrl: string;
  password: string | null;
  hint: string | null;
};

type Part = {
  kind: "text" | "action" | "error" | "done" | "blocked" | "permission";
  text: string;
  reqId?: string; // permission parts
  label?: string; // permission parts — the action kind ("type text", …)
  resolved?: "once" | "session" | "deny"; // permission parts, once answered
};
type ChatMsg = { role: "user" | "assistant"; parts: Part[] };

// SSE frame shapes emitted by computer_use_service.py / the route.
type LoopEvent =
  | { type: "start"; task?: string }
  | { type: "text"; text?: string }
  | { type: "action"; summary?: string }
  | { type: "permission_request"; id?: string; action?: string; kind?: string; label?: string; summary?: string }
  | { type: "blocked"; summary?: string }
  | { type: "denied"; summary?: string }
  | { type: "ping" }
  | { type: "done" }
  | { type: "error"; error?: string };

const EXAMPLES = [
  "Take a screenshot and tell me what's open",
  "Open Firefox and go to news.ycombinator.com",
  "Open the file manager and list my home folder",
];

// Models the sidecar can drive (all are native computer-use capable; we drive
// them via the uniform SOM path). Kept in sync with the sidecar's
// _ALLOWED_MODELS. Each provider is dimmed in the picker when its key is absent.
const CU_MODELS = [
  { id: "claude-sonnet-4-6", label: "Claude Sonnet 4.6", hint: "Balanced", provider: "anthropic" },
  { id: "claude-opus-4-8", label: "Claude Opus 4.8", hint: "Most capable", provider: "anthropic" },
  { id: "claude-haiku-4-5", label: "Claude Haiku 4.5", hint: "Fastest", provider: "anthropic" },
  { id: "gpt-5.5", label: "GPT-5.5", hint: "OpenAI", provider: "openai" },
  { id: "gemini-3-flash-preview", label: "Gemini 3 Flash", hint: "Google", provider: "gemini" },
] as const;

const newSessionId = () =>
  typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : String(Date.now());

export default function ComputerUsePage() {
  const [status, setStatus] = useState<Status | null>(null);
  const [vnc, setVnc] = useState<"connecting" | "connected" | "disconnected">("connecting");
  const [task, setTask] = useState("");
  const [running, setRunning] = useState(false);
  const [takeover, setTakeover] = useState(false);
  const [connected, setConnected] = useState(true); // auto-connect when ready
  const [supervised, setSupervised] = useState(true); // approve per action kind
  const [model, setModel] = useState<string>(CU_MODELS[0].id);
  const [thread, setThread] = useState<ChatMsg[]>([]);
  const [sessionId, setSessionId] = useState(newSessionId);
  const abortRef = useRef<AbortController | null>(null);
  const endRef = useRef<HTMLDivElement | null>(null);
  const reduce = useReducedMotion() ?? false;

  const refreshStatus = useCallback(async () => {
    try {
      const r = await fetch("/api/computer-use", { cache: "no-store" });
      setStatus((await r.json()) as Status);
    } catch {
      setStatus({ ready: false, streamUp: false, sidecarUp: false, wsUrl: "", password: null, hint: "Could not reach the web API." });
    }
  }, []);

  useEffect(() => {
    void refreshStatus();
  }, [refreshStatus]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [thread]);

  const appendPart = useCallback((part: Part) => {
    setThread((prev) => {
      if (prev.length === 0) return prev;
      const last = prev[prev.length - 1];
      if (last.role !== "assistant") return prev;
      const copy = prev.slice();
      copy[copy.length - 1] = { ...last, parts: [...last.parts, part] };
      return copy;
    });
  }, []);

  const stop = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setRunning(false);
  }, []);

  const takeControl = useCallback(() => {
    stop(); // taking over pauses the agent so you don't fight for the cursor
    setTakeover(true);
  }, [stop]);

  const newChat = useCallback(() => {
    stop();
    setThread([]);
    setSessionId(newSessionId());
  }, [stop]);

  const disconnect = useCallback(() => {
    stop(); // disconnecting the view also stops the agent's current turn
    setConnected(false);
    setVnc("disconnected");
  }, [stop]);

  const connect = useCallback(() => {
    setVnc("connecting");
    setConnected(true);
  }, []);

  // Resolve a pending in-chat permission prompt → tell the sidecar + mark the card.
  const resolvePermission = useCallback(async (reqId: string, decision: "once" | "session" | "deny") => {
    setThread((prev) =>
      prev.map((m) => ({
        ...m,
        parts: m.parts.map((p) =>
          p.kind === "permission" && p.reqId === reqId ? { ...p, resolved: decision } : p,
        ),
      })),
    );
    try {
      await fetch("/api/computer-use/approve", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ request_id: reqId, decision }),
      });
    } catch {
      /* the loop will time out and deny on its own */
    }
  }, []);

  const runTask = useCallback(
    async (override?: string) => {
      const t = (override ?? task).trim();
      if (!t || running || !status?.ready) return;
      setTakeover(false); // hand control back to the agent for its turn
      setRunning(true);
      setTask("");
      setThread((prev) => [
        ...prev,
        { role: "user", parts: [{ kind: "text", text: t }] },
        { role: "assistant", parts: [] },
      ]);
      const ctrl = new AbortController();
      abortRef.current = ctrl;
      try {
        const res = await fetch("/api/computer-use", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ task: t, session_id: sessionId, supervised, model }),
          signal: ctrl.signal,
        });
        if (!res.body) throw new Error("no stream");
        const reader = res.body.getReader();
        const dec = new TextDecoder();
        let buf = "";
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          buf += dec.decode(value, { stream: true });
          let idx: number;
          while ((idx = buf.indexOf("\n\n")) >= 0) {
            const frame = buf.slice(0, idx);
            buf = buf.slice(idx + 2);
            const dataLine = frame.split("\n").find((l) => l.startsWith("data:"));
            if (!dataLine) continue;
            let evt: LoopEvent;
            try {
              evt = JSON.parse(dataLine.slice(5).trim()) as LoopEvent;
            } catch {
              continue;
            }
            if (evt.type === "text" && evt.text) appendPart({ kind: "text", text: evt.text });
            else if (evt.type === "action" && evt.summary) appendPart({ kind: "action", text: evt.summary });
            else if (evt.type === "permission_request" && evt.id)
              appendPart({ kind: "permission", reqId: evt.id, label: evt.label ?? "this action", text: evt.summary ?? "" });
            else if (evt.type === "blocked" && evt.summary) appendPart({ kind: "blocked", text: evt.summary });
            else if (evt.type === "error" && evt.error) appendPart({ kind: "error", text: evt.error });
            else if (evt.type === "done") appendPart({ kind: "done", text: "Done" });
            // "denied" is reflected by the permission card's resolved state; "ping" keeps the stream alive.
          }
        }
      } catch (err) {
        if (!ctrl.signal.aborted) appendPart({ kind: "error", text: err instanceof Error ? err.message : "run failed" });
      } finally {
        if (abortRef.current === ctrl) abortRef.current = null;
        setRunning(false);
      }
    },
    [task, running, status?.ready, sessionId, supervised, model, appendPart],
  );

  // Header pill reflects the true state across all three layers: services
  // down → offline; user disconnected → disconnected; else the live VNC state.
  const connStatus = !status?.ready ? "offline" : connected ? vnc : "disconnected";
  const dotColor =
    connStatus === "connected" ? "bg-emerald-500" : connStatus === "connecting" ? "bg-amber-500" : "bg-neutral-500";

  return (
    <div className="flex h-full flex-col">
      <header className="flex h-12 shrink-0 items-center gap-2 border-b border-border/60 px-5">
        <Monitor className="size-3.5 text-primary" />
        <span className="font-mono text-[11px] uppercase tracking-[0.18em] text-foreground/80">Computer use</span>
        <span className="ml-1 inline-flex items-center gap-1.5 rounded-full border border-border/60 px-2 py-0.5 text-[10px] text-muted-foreground">
          <span className={`size-1.5 rounded-full ${dotColor}`} />
          {connStatus}
        </span>
        <div className="flex-1" />
        {thread.length > 0 && (
          <HeaderBtn onClick={newChat} title="Start a new conversation">
            <RotateCcw className="size-3" /> New
          </HeaderBtn>
        )}
        {status?.ready && (
          <HeaderBtn
            onClick={() => setSupervised((v) => !v)}
            title={
              supervised
                ? "Supervised — approve each kind of action before Jarvis does it"
                : "Auto — Jarvis acts without asking (the sensitive-app blocklist still applies)"
            }
            active={supervised}
          >
            <ShieldCheck className="size-3" /> {supervised ? "Supervised" : "Auto"}
          </HeaderBtn>
        )}
        {status?.ready && (
          <HeaderBtn
            onClick={connected ? disconnect : connect}
            title={connected ? "Disconnect the desktop stream" : "Connect the desktop stream"}
          >
            {connected ? (
              <>
                <Unplug className="size-3" /> Disconnect
              </>
            ) : (
              <>
                <Plug className="size-3" /> Connect
              </>
            )}
          </HeaderBtn>
        )}
        {status?.ready && connected && (
          <HeaderBtn
            onClick={takeover ? () => setTakeover(false) : takeControl}
            title={takeover ? "Hand control back to Jarvis" : "Take control of the desktop"}
            active={takeover}
          >
            <Hand className="size-3" /> {takeover ? "Give control" : "Take control"}
          </HeaderBtn>
        )}
        {running ? (
          <HeaderBtn onClick={stop} title="Stop the agent">
            <Square className="size-3" /> Stop
          </HeaderBtn>
        ) : (
          <HeaderBtn onClick={() => void refreshStatus()} title="Re-check the desktop stream">
            <RotateCw className="size-3" /> Refresh
          </HeaderBtn>
        )}
      </header>

      <div className="flex min-h-0 flex-1">
        {/* Live desktop */}
        <div className="relative min-w-0 flex-1 bg-black/40 p-3">
          {status?.ready && status.password && connected ? (
            <>
              <NoVNCView
                wsUrl={status.wsUrl}
                password={status.password}
                viewOnly={!takeover}
                onState={setVnc}
                className={`h-full w-full overflow-hidden rounded-lg border transition-colors ${takeover ? "border-primary" : "border-border/60"}`}
              />
              {takeover && (
                <div className="pointer-events-none absolute inset-x-3 top-3 flex items-center justify-center">
                  <span className="rounded-full bg-primary/90 px-3 py-1 text-[11px] font-medium text-primary-foreground shadow-lg">
                    You’re in control — click “Give control” when done
                  </span>
                </div>
              )}
            </>
          ) : status?.ready && !connected ? (
            <div className="flex h-full items-center justify-center">
              <div className="flex flex-col items-center gap-3 rounded-lg border border-border/60 bg-card/40 p-6 text-sm">
                <div className="text-muted-foreground">Disconnected from the desktop.</div>
                <HeaderBtn onClick={connect} title="Connect to the desktop stream">
                  <Plug className="size-3" /> Connect
                </HeaderBtn>
              </div>
            </div>
          ) : (
            <div className="flex h-full items-center justify-center">
              <div className="max-w-md rounded-lg border border-border/60 bg-card/40 p-5 text-sm">
                <div className="mb-2 font-medium text-foreground">Desktop stream not ready</div>
                <ul className="space-y-1 text-xs text-muted-foreground">
                  <li className={status?.streamUp ? "text-emerald-500" : ""}>{status?.streamUp ? "✓" : "•"} VNC stream (:6080)</li>
                  <li className={status?.sidecarUp ? "text-emerald-500" : ""}>{status?.sidecarUp ? "✓" : "•"} computer-use sidecar (:8771)</li>
                </ul>
                {status?.hint && (
                  <pre className="mt-3 overflow-x-auto rounded-md border border-border/60 bg-background/60 p-2 text-[10.5px] leading-5 text-muted-foreground">
                    {status.hint}
                  </pre>
                )}
                <HeaderBtn onClick={() => void refreshStatus()} title="Re-check" className="mt-3">
                  <RotateCw className="size-3" /> Re-check
                </HeaderBtn>
              </div>
            </div>
          )}
        </div>

        {/* Conversation */}
        <aside className="flex w-[380px] shrink-0 flex-col border-l border-border/60">
          <div className="flex items-center gap-2 border-b border-border/60 px-4 py-2.5 font-mono text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
            Conversation
            {running && (
              <span className="inline-flex items-center gap-1 text-primary">
                <Loader2 className="size-3 animate-spin" /> working
              </span>
            )}
          </div>

          <div className="flex-1 space-y-6 overflow-y-auto px-4 py-5">
            {thread.length === 0 ? (
              <div className="px-1 pt-6">
                <div className="text-center text-xs text-muted-foreground">
                  Tell Jarvis what to do on the desktop. It watches the screen and works step by step — take control any time for logins or captchas.
                </div>
                <div className="mt-5 space-y-1.5">
                  {EXAMPLES.map((ex) => (
                    <button
                      key={ex}
                      onClick={() => void runTask(ex)}
                      disabled={!status?.ready}
                      className="block w-full rounded-md border border-border/60 bg-card/40 px-3 py-2 text-left text-xs text-foreground/90 transition-colors hover:border-primary/40 hover:bg-card disabled:opacity-40"
                    >
                      {ex}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              thread.map((m, i) => <ChatBubble key={i} msg={m} reduce={reduce} onApprove={resolvePermission} />)
            )}
            <div ref={endRef} />
          </div>

          <div className="border-t border-border/60 p-3">
            <div className="rounded-lg border border-border/60 bg-card/40 p-2 focus-within:border-primary/40">
              <div className="flex items-end gap-2">
                <textarea
                  value={task}
                  onChange={(e) => setTask(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      void runTask();
                    }
                  }}
                  rows={2}
                  placeholder={running ? "Working… press Stop to interrupt" : takeover ? "You're in control of the desktop" : "Tell Jarvis what to do…"}
                  disabled={!status?.ready || running || takeover}
                  className="max-h-32 min-h-[2.5rem] flex-1 resize-none bg-transparent text-[13px] text-foreground outline-none placeholder:text-muted-foreground disabled:opacity-50"
                />
                <button
                  onClick={() => void runTask()}
                  disabled={!status?.ready || running || takeover || !task.trim()}
                  title="Send"
                  className="inline-flex size-8 shrink-0 items-center justify-center rounded-md bg-primary text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-40"
                >
                  {running ? <Loader2 className="size-3.5 animate-spin" /> : <CornerDownLeft className="size-3.5" />}
                </button>
              </div>
              <div className="mt-1.5 flex items-center border-t border-border/40 pt-1.5">
                <ModelPicker model={model} setModel={setModel} disabled={running} providers={status?.providers} />
              </div>
            </div>
          </div>
        </aside>
      </div>
    </div>
  );
}

function HeaderBtn({
  children,
  onClick,
  title,
  active,
  className,
}: {
  children: ReactNode;
  onClick: () => void;
  title: string;
  active?: boolean;
  className?: string;
}) {
  return (
    <button
      onClick={onClick}
      title={title}
      className={`inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-[11px] transition-colors ${
        active ? "border-primary/60 bg-primary/10 text-primary" : "border-border/60 bg-card text-foreground hover:border-primary/40"
      } ${className ?? ""}`}
    >
      {children}
    </button>
  );
}

function ChatBubble({
  msg,
  reduce,
  onApprove,
}: {
  msg: ChatMsg;
  reduce: boolean;
  onApprove: (reqId: string, decision: "once" | "session" | "deny") => void;
}) {
  // User bubble — matches the main chat exactly (rounded-2xl bg-card,
  // text-[14.5px] leading-6, right-aligned, max-w-[85%]).
  if (msg.role === "user") {
    return (
      <motion.div
        initial={reduce ? false : { opacity: 0, y: 6 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.16, ease: "easeOut" }}
        className="flex justify-end"
      >
        <div className="max-w-[85%] rounded-2xl bg-card px-4 py-2.5 text-foreground">
          <p className="whitespace-pre-wrap text-[14.5px] leading-6">{msg.parts[0]?.text}</p>
        </div>
      </motion.div>
    );
  }
  // Assistant — full width, no bubble (like the main chat). Reasoning renders
  // through the same <Markdown>; computer-use actions are a compact trace row.
  return (
    <motion.div
      initial={reduce ? false : { opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.16, ease: "easeOut" }}
      className="w-full space-y-2"
    >
      {msg.parts.length === 0 && <ThinkingDots />}
      {msg.parts.map((p, i) => {
        if (p.kind === "text") return <Markdown key={i} content={p.text} />;
        if (p.kind === "action")
          return (
            <div key={i} className="flex items-center gap-2 text-[12px] text-muted-foreground">
              <span className="size-1 shrink-0 rounded-full bg-primary/70" />
              <span className="font-mono">{p.text}</span>
            </div>
          );
        if (p.kind === "done")
          return (
            <div key={i} className="text-[12px] text-emerald-500">
              ✓ {p.text}
            </div>
          );
        if (p.kind === "blocked")
          return (
            <div key={i} className="rounded-md bg-destructive/10 px-2.5 py-1.5 text-[12px] text-destructive">
              ⛔ {p.text}
            </div>
          );
        if (p.kind === "permission") return <PermissionCard key={i} part={p} onApprove={onApprove} />;
        return (
          <div key={i} className="rounded-lg bg-destructive/10 px-3 py-2 text-[13px] text-destructive">
            {p.text}
          </div>
        );
      })}
    </motion.div>
  );
}

// Scoped model picker — only the Claude models the sidecar can actually drive.
function ModelPicker({
  model,
  setModel,
  disabled,
  providers,
}: {
  model: string;
  setModel: (m: string) => void;
  disabled?: boolean;
  providers?: Record<string, boolean>;
}) {
  const current = CU_MODELS.find((m) => m.id === model) ?? CU_MODELS[0];
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={
          <button
            disabled={disabled}
            className="inline-flex items-center gap-1.5 rounded-md px-1.5 py-0.5 text-[11px] text-muted-foreground transition-colors hover:text-foreground disabled:opacity-50"
            title="Model that drives the desktop"
          />
        }
      >
        <Cpu className="size-3" />
        {current.label}
        <ChevronDown className="size-3 opacity-60" />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="min-w-56">
        {CU_MODELS.map((m) => {
          const avail = !providers || providers[m.provider] !== false;
          return (
            <DropdownMenuItem
              key={m.id}
              disabled={!avail}
              onClick={() => {
                if (avail) setModel(m.id);
              }}
              className="flex items-center justify-between gap-3"
            >
              <span className="flex items-center gap-2">
                {m.id === model ? <Check className="size-3.5 text-primary" /> : <span className="size-3.5" />}
                {m.label}
                <span className="rounded-sm bg-primary/10 px-1 py-px text-[8px] font-medium uppercase tracking-wide text-primary/80">
                  native
                </span>
              </span>
              <span className="text-[10px] text-muted-foreground">{avail ? m.hint : "no key"}</span>
            </DropdownMenuItem>
          );
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

// In-chat permission prompt (Cowork-style "approve before Jarvis acts").
function PermissionCard({
  part,
  onApprove,
}: {
  part: Part;
  onApprove: (reqId: string, decision: "once" | "session" | "deny") => void;
}) {
  return (
    <div className="rounded-lg border border-primary/30 bg-primary/5 p-2.5">
      <div className="text-[13px] text-foreground">
        Allow Jarvis to <span className="font-medium">{part.label}</span>?
      </div>
      {part.text ? <div className="mt-0.5 font-mono text-[11px] text-muted-foreground">{part.text}</div> : null}
      {part.resolved ? (
        <div className="mt-2 text-[12px] text-muted-foreground">
          {part.resolved === "deny"
            ? "✗ Denied"
            : part.resolved === "session"
              ? "✓ Approved for the session"
              : "✓ Approved"}
        </div>
      ) : (
        <div className="mt-2 flex flex-wrap gap-1.5">
          <button
            onClick={() => part.reqId && onApprove(part.reqId, "once")}
            className="rounded-md bg-primary px-2.5 py-1 text-[11px] text-primary-foreground transition-opacity hover:opacity-90"
          >
            Approve
          </button>
          <button
            onClick={() => part.reqId && onApprove(part.reqId, "session")}
            className="rounded-md border border-border/60 bg-card px-2.5 py-1 text-[11px] text-foreground transition-colors hover:border-primary/40"
          >
            For session
          </button>
          <button
            onClick={() => part.reqId && onApprove(part.reqId, "deny")}
            className="rounded-md border border-border/60 bg-card px-2.5 py-1 text-[11px] text-destructive transition-colors hover:border-destructive/40"
          >
            Deny
          </button>
        </div>
      )}
    </div>
  );
}

// Bouncing dots — same pre-first-token indicator the main chat uses.
function ThinkingDots() {
  return (
    <span className="flex items-center gap-1 px-0.5" aria-label="Thinking">
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className="size-1.5 rounded-full bg-muted-foreground/60 animate-bounce"
          style={{ animationDelay: `${i * 0.18}s`, animationDuration: "1s" }}
        />
      ))}
    </span>
  );
}