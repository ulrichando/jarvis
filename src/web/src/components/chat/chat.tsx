"use client";

// Hand-rolled streaming chat (no AI SDK hook).
//
// We previously used `useChat` from @ai-sdk/react. On Next 16 +
// React 19 + AI SDK 6.0.168, the hook fired its onFinish callback
// (so the network round-trip completed) but the `messages` it
// exposed to consumers never updated — the empty-state branch
// stayed mounted, the user saw "nothing happened."
//
// Open-webui's chat (Svelte) sidesteps the hook layer entirely:
// fetch the streaming response, read body bytes via getReader(),
// parse the SSE protocol ourselves, and push each text-delta into
// React state synchronously via flushSync. flushSync is the key —
// without it React 18+ auto-batches the per-token updates inside an
// async loop into a single render at the end, which on top of any
// other state-update bug surfaces as "I send and nothing appears."
//
// Pattern, per-turn:
//   1. push BOTH user message + empty assistant placeholder (sync)
//   2. flush input clear
//   3. POST /api/chat with the new history
//   4. Read response.body via ReadableStream reader
//   5. For each `text-delta`, write to a ref + schedule one rAF flush
//      (rAF calls setMessages at most once per animation frame — no blocking)
//   6. On finish/[DONE] flip status back to ready

import { type UIMessage } from "ai";
import { useEffect, useMemo, useRef, useState, startTransition } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { PanelLeftOpen } from "lucide-react";
import { Thread } from "./thread";
import { Composer } from "./composer";
import { EmptyState } from "./empty-state";
import { FunctionGrid } from "./function-grid";
import { TaskPanel } from "./task-panel";
import { useChatStore } from "@/stores/chat";
import { useSettings } from "@/hooks/use-settings";
import { useUI } from "@/stores/ui";
import { DEFAULT_MODEL, MODELS_META } from "@/lib/ai/models-meta";
import { getProviderUX } from "@/lib/ai/provider-ux";
import { StreamingMessageParser } from "@/lib/actions/message-parser";
import { ActionRunner, type ActionEvent } from "@/lib/actions/runner";
import { apiWriteFile } from "@/lib/workspace/client";
import type { TrackedAction, ArtifactData } from "@/lib/actions/types";
import { ArtifactPanel } from "./artifact-panel";

type ChatProps = {
  chatId?: string;
  initialMessages?: UIMessage[];
  // When the chat is embedded inside the workbench page we already
  // know which workspace the user is editing — pass it in to override
  // the store's target. The composer's workspace picker is hidden in
  // this mode since switching it would point chat at a different
  // workspace than the editor visible next to it.
  workspaceId?: string;
  workspaceName?: string;
  embedded?: boolean;
  // First message to send automatically on mount — used when a chat is
  // launched from a Project composer that pre-creates the conversation
  // and forwards the prompt via the URL.
  seed?: string;
  // Override the composer placeholder for context-specific surfaces.
  composerPlaceholder?: string;
  // Selects which workspace prompt the API attaches. Default is the
  // coding workbench; "design" swaps in the single-file HTML designer
  // prompt for the Design tab.
  mode?: "design";
  // Selects the design playbook the API uses. Only meaningful when mode === "design".
  format?: import("@/lib/design/format").Format;
  // Fires on each chunk of a file action while it streams. Lets a parent
  // (e.g. DesignView) pipe partial content into a live iframe preview so the
  // canvas updates as Claude generates. `partial` includes everything written
  // so far for this file in this turn.
  onStreamingFile?: (filePath: string, partial: string) => void;
  // Fires when a file action finishes streaming. Used to clear streaming state.
  onFileComplete?: (filePath: string) => void;
  // Programmatic prefill of the composer input. Bumping `id` to a new value
  // sets the input to `text` (does NOT auto-send). Used by Design starter
  // cards to prefill an example prompt the user can review and submit.
  prefillPrompt?: { id: string; text: string; autoSend?: boolean };
  // Suppress the sidebar-toggle button that the embedded chat renders by
  // default. The Design tab has its own chrome, so the global-sidebar opener
  // would just be a confusing duplicate.
  hideSidebarToggle?: boolean;
  // Force the composer into a model-agnostic shell — same shape regardless of
  // which provider is selected. Used by the Design tab so switching models
  // doesn't shift the composer's pre-block / inline toggles.
  unifiedUX?: boolean;
  // Fires when the server returns a conversation ID via the X-Conversation-Id
  // response header (i.e. on the first POST that creates the conversation,
  // or any subsequent POST). Lets a parent persist the id (e.g. localStorage
  // keyed by workspace) so the conversation can be reloaded on page refresh.
  onConversationId?: (id: string) => void;
};

type ChatStatus = "ready" | "submitted" | "streaming" | "error";

function makeId(prefix: string) {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

// Truncate huge output blobs so a runaway log doesn't blow the model's
// context. We keep the head (where the failing line + stack typically
// is) and a tail. 6KB/stream/action is enough for a build error +
// stack trace; bigger outputs probably mean the model should run a
// more targeted command.
function clipOutput(s: string, maxBytes = 6_000): string {
  if (!s) return "";
  if (s.length <= maxBytes) return s;
  const head = s.slice(0, Math.floor(maxBytes * 0.7));
  const tail = s.slice(-Math.floor(maxBytes * 0.3));
  return `${head}\n…[clipped ${s.length - maxBytes} bytes]…\n${tail}`;
}

type CapturedResultLite = {
  actionId: string;
  type: "shell" | "start";
  command: string;
  exitCode: number;
  stdout: string;
  stderr: string;
  detached: boolean;
};

// Serializes the captured shell/start results into a block the LLM can
// read on its NEXT turn. This is what every Bolt-style coding agent
// does: actions are NOT fire-and-forget — their output gets fed back
// so the model can verify, diagnose, and self-correct.
function renderActionResultsBlock(results: CapturedResultLite[]): string {
  const lines: string[] = ["<boltActionResults>"];
  for (const r of results) {
    lines.push(`  <result actionId="${r.actionId}" type="${r.type}" exitCode="${r.exitCode}">`);
    lines.push(`    <command>${r.command}</command>`);
    if (r.detached) {
      lines.push(`    <note>started in background; output not captured</note>`);
    } else {
      const stdout = clipOutput(r.stdout).trimEnd();
      const stderr = clipOutput(r.stderr).trimEnd();
      if (stdout) lines.push(`    <stdout>\n${stdout}\n    </stdout>`);
      if (stderr) lines.push(`    <stderr>\n${stderr}\n    </stderr>`);
      if (!stdout && !stderr) lines.push(`    <note>(no output)</note>`);
    }
    lines.push(`  </result>`);
  }
  lines.push("</boltActionResults>");
  return lines.join("\n");
}

// Poll /api/workspace/<id>/preview until something is listening or the
// budget runs out. The dev server (`vite`, `next dev`, etc.) takes a
// few seconds to boot after `npm run dev` starts; we don't want to
// block the chat UI on it.
function pollForPreviewPort(
  workspaceId: string,
  onPort: (port: number) => void,
  { intervalMs = 1500, timeoutMs = 60_000 } = {},
): { cancel: () => void } {
  let cancelled = false;
  const start = Date.now();
  const tick = async () => {
    if (cancelled) return;
    try {
      const r = await fetch(`/api/workspace/${workspaceId}/preview`);
      const j = await r.json();
      if (!cancelled && j?.hostPort) {
        onPort(j.hostPort);
        return;
      }
    } catch {}
    if (cancelled) return;
    if (Date.now() - start > timeoutMs) return;
    setTimeout(tick, intervalMs);
  };
  // First probe a touch later — the start action is detached and the
  // dev server hasn't begun listening yet.
  setTimeout(tick, 1000);
  return {
    cancel: () => {
      cancelled = true;
    },
  };
}

export function Chat({
  chatId,
  initialMessages,
  workspaceId: workspaceIdProp,
  workspaceName: workspaceNameProp,
  embedded = false,
  seed,
  composerPlaceholder,
  mode,
  format,
  onStreamingFile,
  onFileComplete,
  onConversationId,
  prefillPrompt,
  hideSidebarToggle,
  unifiedUX,
}: ChatProps) {
  const qc = useQueryClient();
  const [input, setInput] = useState("");
  const [messages, setMessages] = useState<UIMessage[]>(initialMessages ?? []);
  const [status, setStatus] = useState<ChatStatus>("ready");
  const [artifacts, setArtifacts] = useState<
    Map<string, { artifact: ArtifactData; actions: TrackedAction[] }>
  >(new Map());
  const [previewPort, setPreviewPort] = useState<number | null>(null);
  const [activeCategory, setActiveCategory] = useState<string | null>(null);
  // Per-assistant-message reasoning text. Reasoning models (DeepSeek
  // V4 Pro, R1, gpt-oss) emit the model's chain-of-thought as
  // `reasoning-delta` chunks. We accumulate them keyed by the
  // assistantId so each message keeps its own "Thoughts" trace, and
  // re-render via the same rAF cadence as text-delta.
  const [reasoningById, setReasoningById] = useState<Map<string, string>>(
    () => new Map(),
  );
  // Per-assistant-message plan card. The model emits a <jarvisplan>
  // block before the boltArtifact describing what's about to be built —
  // we surface it as a card above the message body so the user sees the
  // "blueprint" before files start streaming in.
  const [planById, setPlanById] = useState<
    Map<string, { content: string; complete: boolean }>
  >(() => new Map());
  const previewPollRef = useRef<{ cancel: () => void } | null>(null);

  const { toggleSidebar, sidebarOpen } = useUI();
  const model = useChatStore((s) => s.model);
  const setModel = useChatStore((s) => s.setModel);
  const storeWorkspaceId = useChatStore((s) => s.targetWorkspaceId);
  const storeWorkspaceName = useChatStore((s) => s.targetWorkspaceName);
  // When mounted inside the workbench, props pin the workspace; otherwise
  // fall back to whatever the user picked in the composer dropdown.
  const targetWorkspaceId = workspaceIdProp ?? storeWorkspaceId;
  const targetWorkspaceName = workspaceNameProp ?? storeWorkspaceName;
  const { data: settings } = useSettings();

  const activeMeta = MODELS_META[model] ?? MODELS_META[DEFAULT_MODEL];
  const provider = activeMeta.provider;
  // UX is locked to a single stable interface regardless of which model is
  // selected — switching models changes the backend, not the homepage layout.
  const ux = getProviderUX("anthropic");

  // If the persisted model id is no longer in the registry (renamed
  // or removed between sessions), reset to default — otherwise every
  // submit would 400 with "missing_api_key" on a model the picker
  // can't even render.
  useEffect(() => {
    if (!MODELS_META[model]) {
      setModel(DEFAULT_MODEL);
    }
  }, [model, setModel]);

  const abortRef = useRef<AbortController | null>(null);
  // rAF-based streaming flush: the hot loop writes here; a scheduled
  // animation-frame reads it and calls setMessages once per frame.
  // This never blocks the event loop (unlike flushSync).
  const streamPending = useRef<{ id: string; text: string } | null>(null);
  const rafId = useRef<number | null>(null);
  // Same pattern for the reasoning-delta stream — separate ref + rAF
  // slot so reasoning and text don't fight for the same frame.
  const reasoningPending = useRef<{ id: string; text: string } | null>(null);
  const reasoningRafId = useRef<number | null>(null);

  const stop = () => {
    abortRef.current?.abort();
    abortRef.current = null;
    if (rafId.current !== null) {
      cancelAnimationFrame(rafId.current);
      rafId.current = null;
    }
    if (reasoningRafId.current !== null) {
      cancelAnimationFrame(reasoningRafId.current);
      reasoningRafId.current = null;
    }
    streamPending.current = null;
    reasoningPending.current = null;
    setStatus("ready");
  };

  const submit = async (text?: string) => {
    const content = (text ?? input).trim();
    if (!content || status === "streaming" || status === "submitted") return;

    // 1. Optimistic append: BOTH user message AND empty assistant
    // placeholder, synchronously, before the fetch starts. The
    // user sees their message immediately + the thinking-dots on
    // the empty assistant bubble.
    const userMessage: UIMessage = {
      id: makeId("u"),
      role: "user",
      parts: [{ type: "text", text: content }],
    };
    const assistantId = makeId("a");
    const assistantPlaceholder: UIMessage = {
      id: assistantId,
      role: "assistant",
      parts: [{ type: "text", text: "" }],
    };

    setInput("");
    setStatus("submitted");
    setPreviewPort(null);
    previewPollRef.current?.cancel();
    const historyForApi = [...messages, userMessage];
    setMessages([...historyForApi, assistantPlaceholder]);

    // 1b. Set up streaming-action plumbing for this turn. The parser
    // walks the assistant's text as it streams and the runner executes
    // each <boltAction> against the target workspace container. Both
    // are local to this submit() call so previous turns' state can't
    // leak in.
    const localArtifacts = new Map<
      string,
      { artifact: ArtifactData; actions: TrackedAction[] }
    >();
    // rAF-coalesced flush. The parser fires onActionStream on every chunk
    // (10-30/sec); calling setArtifacts + flushSync per chunk freezes the
    // main thread (clicks blocked, tab crashes). Coalesce into one paint.
    let flushScheduled = false;
    const scheduleArtifactFlush = () => {
      if (flushScheduled) return;
      flushScheduled = true;
      requestAnimationFrame(() => {
        flushScheduled = false;
        setArtifacts(new Map(localArtifacts));
      });
    };
    const updateArtifact = (
      artifactId: string,
      mut: (prev: { artifact: ArtifactData; actions: TrackedAction[] }) => {
        artifact: ArtifactData;
        actions: TrackedAction[];
      },
    ) => {
      const prev = localArtifacts.get(artifactId);
      if (!prev) return;
      const next = mut(prev);
      localArtifacts.set(artifactId, next);
      scheduleArtifactFlush();
    };

    const invalidateForFile = (filePath: string) => {
      qc.invalidateQueries({ queryKey: ["ws", targetWorkspaceId, "tree"] });
      qc.invalidateQueries({ queryKey: ["design-tree", targetWorkspaceId] });
      qc.invalidateQueries({
        queryKey: ["design-file", targetWorkspaceId, filePath],
      });
    };
    const runner =
      targetWorkspaceId !== null
        ? new ActionRunner(
            targetWorkspaceId,
            (ev: ActionEvent) => {
              const t = ev.tracked;
              updateArtifact(t.artifactId, (prev) => {
                const actions = prev.actions.slice();
                const idx = actions.findIndex((a) => a.actionId === t.actionId);
                if (idx === -1) actions.push(t);
                else actions[idx] = t;
                return { ...prev, actions };
              });
              if (ev.kind === "error") {
                toast.error(`Action failed: ${ev.error}`);
              }
              if (ev.kind === "success" && t.action.type === "file") {
                invalidateForFile(t.action.filePath);
              }
              // After a `start` action succeeds, the dev server is just
              // booting. Poll the preview endpoint until something is
              // actually listening, then surface a one-click preview link.
              if (ev.kind === "success" && t.action.type === "start") {
                previewPollRef.current?.cancel();
                previewPollRef.current = pollForPreviewPort(
                  targetWorkspaceId,
                  (port) => setPreviewPort(port),
                );
              }
              // Capture shell + start results so we can hand them back
              // to the model on the next turn. Skipped for file actions
              // — the model already knows what it wrote.
              if (
                (ev.kind === "success" || ev.kind === "error") &&
                (t.action.type === "shell" || t.action.type === "start") &&
                ev.result
              ) {
                turnResults.push({
                  actionId: t.actionId,
                  type: t.action.type,
                  command: t.action.content,
                  exitCode: ev.result.exitCode,
                  stdout: ev.result.stdout,
                  stderr: ev.result.stderr,
                  detached: Boolean(ev.result.detached),
                });
              }
            },
            // Placeholder-write hook: fires when an empty file is written
            // on action-open, so the panel populates without waiting for
            // the close tag.
            (filePath) => invalidateForFile(filePath),
          )
        : null;

    const parser = new StreamingMessageParser({
      onPlan: (p) => {
        // Live-update the plan card as <jarvisplan> streams. complete=true
        // fires once the closing tag arrives so the card can render its
        // settled state (e.g., remove the streaming pulse).
        startTransition(() => {
          setPlanById((prev) => {
            const next = new Map(prev);
            next.set(assistantId, {
              content: p.content,
              complete: p.complete,
            });
            return next;
          });
        });
      },
      onArtifactOpen: (a) => {
        const artifact: ArtifactData = { id: a.id, title: a.title, type: a.type };
        localArtifacts.set(a.id, { artifact, actions: [] });
        scheduleArtifactFlush();
      },
      onActionOpen: (a) => {
        // Tell the runner to write an empty placeholder so the file
        // appears in the design panel right away — instead of only
        // landing when the close tag arrives 10-30 seconds later.
        runner?.onOpen(a.artifactId, a.actionId, a.action);
      },
      onActionStream: (a) => {
        runner?.onStream(a.artifactId, a.actionId, a.action);
        if (a.action.type === "file" && onStreamingFile) {
          onStreamingFile(a.action.filePath, a.action.content);
        }
      },
      onActionClose: (a) => {
        runner?.onClose(a.artifactId, a.actionId, a.action);
        if (a.action.type === "file" && onFileComplete) {
          onFileComplete(a.action.filePath);
        }
      },
    });

    // 2. Network round-trip
    const ctrl = new AbortController();
    abortRef.current = ctrl;

    let assistantText = "";
    let parsedSoFar = "";
    // Reasoning trace for this turn. Persisted across auto-continue
    // passes (same assistantId) so the "Thoughts" block shows the full
    // chain of thought, not just the last segment.
    let reasoningText = "";
    // Shell/start action results captured during this turn. Each entry
    // carries the actual command + exit + stdout/stderr so we can
    // append a <boltActionResults> block to the assistant message and
    // give the model real ground-truth on the NEXT turn (Bolt-style
    // tool feedback). Without this the model writes diagnose
    // commands, never sees their output, and either hallucinates or
    // gives up — that's the "Jarvis stops while diagnosing" symptom.
    type CapturedResult = {
      actionId: string;
      type: "shell" | "start";
      command: string;
      exitCode: number;
      stdout: string;
      stderr: string;
      detached: boolean;
    };
    const turnResults: CapturedResult[] = [];

    // Schedule one rAF flush. The hot loop only writes to the ref —
    // never calling setState directly. The rAF callback fires at most
    // once per animation frame (~60fps) and does the actual setState.
    // This keeps the event loop free (no flushSync blocking).
    const scheduleFlush = () => {
      if (rafId.current !== null) return;
      rafId.current = requestAnimationFrame(() => {
        rafId.current = null;
        const p = streamPending.current;
        if (!p) return;
        // startTransition marks this as a low-priority, interruptible
        // render. If new tokens arrive while React is mid-render (markdown
        // parsing a large string), React abandons the in-progress render
        // and restarts with the latest text. This prevents render backlog
        // and is how Claude.ai / Copilot handle fast streaming.
        startTransition(() => {
          setMessages((prev) =>
            prev.map((m) =>
              m.id === p.id
                ? { ...m, parts: [{ type: "text", text: p.text } as never] }
                : m,
            ),
          );
        });
      });
    };

    // Same idea for reasoning-delta. Reasoning models emit dozens of
    // chunks per second; collapsing them to one rAF tick keeps the
    // Thoughts panel smooth.
    const scheduleReasoningFlush = () => {
      if (reasoningRafId.current !== null) return;
      reasoningRafId.current = requestAnimationFrame(() => {
        reasoningRafId.current = null;
        const p = reasoningPending.current;
        if (!p) return;
        startTransition(() => {
          setReasoningById((prev) => {
            const next = new Map(prev);
            next.set(p.id, p.text);
            return next;
          });
        });
      });
    };

    // One pass through fetch + stream-consume. Returns whether the stream
    // ended with finishReason="length" (= the model hit its token cap and
    // we should fire a continuation). Returns null on hard error / abort,
    // letting the caller bail without an auto-continue retry.
    const runOneStream = async (
      messagesForRequest: UIMessage[],
    ): Promise<"length" | "complete" | "error" | "abort"> => {
      // Watchdog: if the server doesn't return ANY response within 20s,
      // something's wrong (provider rejected, route hung, network dead).
      // Surface a toast so the user isn't just staring at the spinner.
      const stuck = setTimeout(() => {
        toast.warning("Server hasn't responded in 20s", {
          description:
            "The provider may be slow or rejecting the request. Click Stop and try a smaller brief or a different model.",
          duration: 8000,
        });
      }, 20000);
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          id: chatId,
          model,
          messages: messagesForRequest,
          workspaceId: targetWorkspaceId ?? undefined,
          mode,
          format,
        }),
        signal: ctrl.signal,
      }).finally(() => clearTimeout(stuck));

      // Capture the server-assigned conversation ID so the parent can
      // persist it (per-workspace) and reload on refresh.
      const cid = res.headers.get("X-Conversation-Id");
      if (cid && onConversationId) onConversationId(cid);

      if (!res.ok || !res.body) {
        let detail = `HTTP ${res.status}`;
        try {
          const j = await res.json();
          detail = j?.message ?? j?.error ?? detail;
          if (j?.error === "missing_api_key" && model !== DEFAULT_MODEL) {
            toast.error(detail, {
              description: `Switching to ${MODELS_META[DEFAULT_MODEL]?.label ?? DEFAULT_MODEL}.`,
            });
            setModel(DEFAULT_MODEL);
          } else {
            toast.error(detail);
          }
        } catch {
          toast.error(detail);
        }
        return "error";
      }

      setStatus("streaming");
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      let finishReason: string | undefined;

      while (true) {
        if (ctrl.signal.aborted) return "abort";
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split("\n");
        buf = lines.pop() ?? "";
        for (const line of lines) {
          if (ctrl.signal.aborted) return "abort";
          if (!line.startsWith("data: ")) continue;
          const raw = line.slice(6);
          if (raw === "[DONE]") continue;
          let evt: { type?: string; delta?: string };
          try {
            evt = JSON.parse(raw);
          } catch {
            continue;
          }
          // text-delta = visible reply tokens. reasoning-delta =
          // hidden chain-of-thought (DeepSeek V4/R1, gpt-oss-120b,
          // o-series). Both arrive as { delta: string }; we render
          // text into the message body and reasoning into a
          // collapsible "Thoughts" block above it.
          if (evt.type === "text-delta" && typeof evt.delta === "string") {
            assistantText += evt.delta;
            // The parser is stateful per messageId. Across continuations
            // we keep the SAME assistantId, so passing the cumulative
            // assistantText resumes mid-artifact correctly — actions
            // opened in the prior pass close cleanly when their `</…>`
            // finally arrives.
            parsedSoFar += parser.parse(assistantId, assistantText);
            streamPending.current = { id: assistantId, text: parsedSoFar };
            scheduleFlush();
          } else if (
            evt.type === "reasoning-delta" &&
            typeof evt.delta === "string"
          ) {
            reasoningText += evt.delta;
            reasoningPending.current = { id: assistantId, text: reasoningText };
            scheduleReasoningFlush();
          } else if (evt.type === "finish") {
            finishReason = (evt as unknown as { finishReason?: string })
              .finishReason;
          }
        }
      }
      return finishReason === "length" ? "length" : "complete";
    };

    try {
      // Auto-continue loop: when the stream ends with finishReason="length"
      // the model ran out of output budget mid-artifact. Fire a follow-up
      // call with the partial assistant text + a tight "continue exactly
      // where you stopped" instruction so the model picks up its own
      // sentence. Capped to 3 retries so a runaway model can't loop forever.
      const MAX_AUTO_CONTINUES = 3;
      let messagesForRequest: UIMessage[] = historyForApi;
      let continued = 0;
      let initialStatus: Awaited<ReturnType<typeof runOneStream>> = "complete";

      while (true) {
        const status = await runOneStream(messagesForRequest);
        if (continued === 0) initialStatus = status;
        if (status === "error" || status === "abort" || status === "complete") {
          if (status === "error") {
            // Drop the empty placeholder so the user isn't left with
            // a permanent "thinking…" bubble.
            setMessages((prev) => prev.filter((m) => m.id !== assistantId));
            setReasoningById((prev) => {
              if (!prev.has(assistantId)) return prev;
              const next = new Map(prev);
              next.delete(assistantId);
              return next;
            });
            setPlanById((prev) => {
              if (!prev.has(assistantId)) return prev;
              const next = new Map(prev);
              next.delete(assistantId);
              return next;
            });
          }
          break;
        }
        // status === "length" → auto-continue.
        if (continued >= MAX_AUTO_CONTINUES) {
          toast.warning("Generation hit its limit", {
            description:
              "Tried to auto-continue 3 times and still hit the token cap. The brief might be too big — try splitting it.",
          });
          break;
        }
        continued += 1;
        toast.info(`Continuing… (${continued}/${MAX_AUTO_CONTINUES})`, {
          duration: 2500,
        });
        messagesForRequest = [
          ...historyForApi,
          {
            id: `${assistantId}-partial-${continued}`,
            role: "assistant",
            parts: [{ type: "text", text: assistantText }],
          } as UIMessage,
          {
            id: makeId("u"),
            role: "user",
            parts: [
              {
                type: "text",
                text: "Continue your previous output exactly where you stopped. Do NOT restart, do NOT write any preamble, do NOT repeat what you already wrote, do NOT recap. The next character you emit must extend the previous text verbatim. Close any open boltAction and the boltArtifact properly.",
              },
            ],
          } as UIMessage,
        ];
      }

      // Cancel any in-flight rAF and do a direct final commit so the
      // last batch of tokens is always visible.
      if (rafId.current !== null) {
        cancelAnimationFrame(rafId.current);
        rafId.current = null;
      }
      if (reasoningRafId.current !== null) {
        cancelAnimationFrame(reasoningRafId.current);
        reasoningRafId.current = null;
      }
      if (parsedSoFar) {
        const finalText = parsedSoFar;
        startTransition(() => {
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantId
                ? { ...m, parts: [{ type: "text", text: finalText } as never] }
                : m,
            ),
          );
        });
      }
      if (reasoningText) {
        const finalReasoning = reasoningText;
        startTransition(() => {
          setReasoningById((prev) => {
            const next = new Map(prev);
            next.set(assistantId, finalReasoning);
            return next;
          });
        });
      }
      streamPending.current = null;
      reasoningPending.current = null;

      // Wait for every queued shell/file action to finish, then build a
      // <boltActionResults> block summarizing exit + stdout + stderr.
      // Append it to the assistant text so the NEXT turn's request
      // includes ground-truth output for each shell command — without
      // this the model is blind to its own diagnostics and either
      // hallucinates or stops mid-investigation.
      if (runner) {
        try {
          await runner.drain();
        } catch (err) {
          console.warn("[chat] runner drain failed:", err);
        }
        if (turnResults.length > 0) {
          const block = renderActionResultsBlock(turnResults);
          assistantText += `\n\n${block}\n`;
          // Also store on the message itself so it persists in history
          // and travels in the next request body.
          startTransition(() => {
            setMessages((prev) =>
              prev.map((m) => {
                if (m.id !== assistantId) return m;
                const existing =
                  m.parts
                    .map((p) => (p.type === "text" ? p.text : ""))
                    .join("") ?? "";
                return {
                  ...m,
                  parts: [
                    {
                      type: "text",
                      text: `${existing}\n\n${block}\n`,
                    } as never,
                  ],
                };
              }),
            );
          });
        }
      }

      // Rescue path for the clarify-mode questionnaire. Some models
      // (deepseek-chat in particular) ignore the playbook's "wrap in
      // boltAction" instruction and dump the questions.html source as
      // a markdown fenced code block instead. The boltAction parser
      // can't see it → no file lands → no interactive form. Detect
      // the questions-form signature in the assistant text and write
      // it as a real file. Strictly scoped to the questions.html case
      // so it can't accidentally rescue other code blocks.
      if (
        targetWorkspaceId !== null &&
        mode === "design" &&
        /<form[^>]*\bid=["']jarvis-questions["']/i.test(assistantText) &&
        !/<boltaction[^>]+filepath=["']questions\.html["']/i.test(
          assistantText,
        )
      ) {
        // Try to extract the full HTML (preferred: from <!doctype to </html>)
        // or fall back to the fenced block content.
        let html: string | null = null;
        const docMatch = assistantText.match(
          /<!doctype[\s\S]*?<\/html>\s*/i,
        );
        if (docMatch) html = docMatch[0];
        if (!html) {
          const fenceMatch = assistantText.match(
            /```(?:html|text|markdown)?\s*\n([\s\S]*?<form[^>]*\bid=["']jarvis-questions["'][\s\S]*?)\n```/i,
          );
          if (fenceMatch) html = fenceMatch[1];
        }
        if (html) {
          try {
            await apiWriteFile(targetWorkspaceId, "questions.html", html);
            invalidateForFile("questions.html");
          } catch (err) {
            console.warn("[chat] questions.html rescue write failed:", err);
          }
        }
      }

      if (initialStatus === "error") {
        setStatus("error");
      } else {
        setStatus("ready");
        qc.invalidateQueries({ queryKey: ["conversations"] });
        if (chatId) qc.invalidateQueries({ queryKey: ["conversation", chatId] });
      }
    } catch (e) {
      const err = e as Error & { name?: string };
      if (err?.name === "AbortError") {
        // User clicked Stop — keep whatever assistantText we got.
        setStatus("ready");
      } else {
        toast.error(err?.message ?? "Couldn't get a reply.");
        setStatus("error");
        setMessages((prev) => prev.filter((m) => m.id !== assistantId));
        setReasoningById((prev) => {
          if (!prev.has(assistantId)) return prev;
          const next = new Map(prev);
          next.delete(assistantId);
          return next;
        });
        setPlanById((prev) => {
          if (!prev.has(assistantId)) return prev;
          const next = new Map(prev);
          next.delete(assistantId);
          return next;
        });
      }
    } finally {
      abortRef.current = null;
    }
  };

  // Auto-fire the seed prompt once on mount when launched from a Project
  // composer (the conversation is pre-created server-side, the prompt
  // arrives via ?seed=). Ref-guarded so React 19 strict-mode mounting
  // doesn't double-submit. queueMicrotask defers the setState chain
  // off the effect body — submit() does an optimistic flushSync, which
  // the lint rule (correctly) flags as cascading-render-prone if called
  // inline.
  const seedFiredRef = useRef(false);
  useEffect(() => {
    if (seedFiredRef.current) return;
    if (!seed?.trim()) return;
    if (messages.length > 0) return;
    seedFiredRef.current = true;
    queueMicrotask(() => {
      void submit(seed);
    });
    // submit closes over latest state via refs/setters; we only want
    // this to run once when the seed arrives.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seed]);

  // Programmatic composer prefill (does NOT auto-send) — bump id to write
  // a new value to the input. Used by Design starter cards.
  const prefillIdRef = useRef<string | null>(null);
  useEffect(() => {
    if (!prefillPrompt) return;
    if (prefillIdRef.current === prefillPrompt.id) return;
    prefillIdRef.current = prefillPrompt.id;
    if (prefillPrompt.autoSend) {
      // Auto-submit the prefilled text — used by the questions.html Continue
      // button so the user doesn't have to manually click Send after answering.
      // Defer to next microtask so submit() runs after this render commits.
      const text = prefillPrompt.text;
      // eslint-disable-next-line no-console
      console.log("[chat] auto-sending prefilled brief:", text.slice(0, 80) + "…");
      queueMicrotask(() => {
        void submit(text);
      });
    } else {
      setInput(prefillPrompt.text);
    }
  }, [prefillPrompt]);

  const isEmpty = messages.length === 0;

  // When embedded in the workbench we always want the composer pinned
  // to the bottom (like a real chat) regardless of whether there are
  // messages yet. The standalone /chat empty state still uses the
  // centered hero treatment.
  if (isEmpty && !embedded) {
    return (
      <div className="flex h-full flex-col items-center justify-center overflow-y-auto px-4 py-8">
        <div className="flex w-full max-w-3xl flex-col">
          <EmptyState name={settings?.user?.name} provider={provider} />
          <div className="mt-8">
            <Composer
              value={input}
              onChange={setInput}
              onSubmit={() => submit()}
              onStop={stop}
              status={status}
              provider={provider}
              hideWorkspacePicker={embedded}
              unifiedUX={unifiedUX}
              placeholder={composerPlaceholder}
            />
          </div>
          <FunctionGrid
            chips={ux.chips}
            onPick={(p) => setInput(p)}
            activeLabel={activeCategory}
            onSetActive={setActiveCategory}
          />
          {(() => {
            const activeChip = ux.chips.find((c) => c.label === activeCategory);
            return activeChip ? (
              <TaskPanel
                chip={activeChip}
                onPick={(p) => { setInput(p); setActiveCategory(null); }}
                onClose={() => setActiveCategory(null)}
              />
            ) : null;
          })()}
        </div>
      </div>
    );
  }

  if (isEmpty && embedded) {
    return (
      <div className="flex h-full flex-col">
        {!sidebarOpen && !hideSidebarToggle && (
          <div className="flex h-10 shrink-0 items-center px-2">
            <button
              onClick={toggleSidebar}
              aria-label="Open sidebar"
              className="flex size-8 items-center justify-center rounded-md text-muted-foreground hover:text-foreground hover:bg-accent/50 transition-colors"
            >
              <PanelLeftOpen className="size-4" />
            </button>
          </div>
        )}
        <div className="flex-1 overflow-y-auto px-4 py-6">
          <p className="text-[13px] text-muted-foreground">
            What do you want to build?
          </p>
          <p className="mt-1 text-[12px] text-muted-foreground/70">
            Files you ask for go straight into{" "}
            <span className="font-mono">{targetWorkspaceName ?? "this workspace"}</span>.
          </p>
        </div>
        <Composer
          value={input}
          onChange={setInput}
          onSubmit={() => submit()}
          onStop={stop}
          status={status}
          provider={provider}
          hideWorkspacePicker={embedded}
          unifiedUX={unifiedUX}
        />
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      {embedded && !sidebarOpen && !hideSidebarToggle && (
        <div className="flex h-10 shrink-0 items-center px-2 border-b border-border/30">
          <button
            onClick={toggleSidebar}
            aria-label="Open sidebar"
            className="flex size-8 items-center justify-center rounded-md text-muted-foreground hover:text-foreground hover:bg-accent/50 transition-colors"
          >
            <PanelLeftOpen className="size-4" />
          </button>
        </div>
      )}
      <div className="flex-1 overflow-y-auto">
        <Thread
          messages={messages}
          isStreaming={status === "streaming" || status === "submitted"}
          reasoningById={reasoningById}
          planById={planById}
          artifactPanel={
            <div className="mx-auto max-w-3xl px-4">
              <ArtifactPanel
                artifacts={artifacts}
                workspaceId={targetWorkspaceId}
                workspaceName={targetWorkspaceName}
                previewPort={previewPort}
                embedded={embedded}
              />
            </div>
          }
        />
      </div>
      <Composer
        value={input}
        onChange={setInput}
        onSubmit={() => submit()}
        onStop={stop}
        status={status}
        provider={provider}
        hideWorkspacePicker={embedded}
        unifiedUX={unifiedUX}
        placeholder={composerPlaceholder}
      />
    </div>
  );
}
