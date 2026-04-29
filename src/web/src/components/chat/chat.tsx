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
import { flushSync } from "react-dom";
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
  prefillPrompt?: { id: string; text: string };
  // Suppress the sidebar-toggle button that the embedded chat renders by
  // default. The Design tab has its own chrome, so the global-sidebar opener
  // would just be a confusing duplicate.
  hideSidebarToggle?: boolean;
};

type ChatStatus = "ready" | "submitted" | "streaming" | "error";

function makeId(prefix: string) {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
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
  prefillPrompt,
  hideSidebarToggle,
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

  const stop = () => {
    abortRef.current?.abort();
    abortRef.current = null;
    if (rafId.current !== null) {
      cancelAnimationFrame(rafId.current);
      rafId.current = null;
    }
    streamPending.current = null;
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
      flushSync(() => {
        setArtifacts(new Map(localArtifacts));
      });
    };

    const runner =
      targetWorkspaceId !== null
        ? new ActionRunner(targetWorkspaceId, (ev: ActionEvent) => {
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
            // After file writes, refresh the file tree so the workbench
            // shows the new files immediately.
            if (ev.kind === "success" && t.action.type === "file") {
              qc.invalidateQueries({ queryKey: ["ws", targetWorkspaceId, "tree"] });
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
          })
        : null;

    const parser = new StreamingMessageParser({
      onArtifactOpen: (a) => {
        const artifact: ArtifactData = { id: a.id, title: a.title, type: a.type };
        localArtifacts.set(a.id, { artifact, actions: [] });
        flushSync(() => setArtifacts(new Map(localArtifacts)));
      },
      onActionOpen: (a) => {
        // No-op for now; we add the action card on close. (For very long
        // file streams we could pre-render the card, but the visual
        // churn isn't worth it.)
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

    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          id: chatId,
          model,
          messages: historyForApi,
          workspaceId: targetWorkspaceId ?? undefined,
          mode,
          format,
        }),
        signal: ctrl.signal,
      });

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
        setStatus("error");
        // Drop the empty placeholder so the user isn't left with
        // a permanent "thinking…" bubble.
        setMessages((prev) => prev.filter((m) => m.id !== assistantId));
        return;
      }

      // 3. Stream consumption
      setStatus("streaming");
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";

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

      while (true) {
        if (ctrl.signal.aborted) break;
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split("\n");
        buf = lines.pop() ?? "";
        for (const line of lines) {
          if (ctrl.signal.aborted) break;
          if (!line.startsWith("data: ")) continue;
          const raw = line.slice(6);
          if (raw === "[DONE]") continue;
          let evt: { type?: string; delta?: string };
          try {
            evt = JSON.parse(raw);
          } catch {
            continue;
          }
          // We only show plain text-delta. Reasoning chunks (DeepSeek
          // V4 / R1, gpt-oss-120b) are intentionally hidden — they
          // arrive as `reasoning-delta` and clutter the visible reply
          // if shown verbatim.
          if (evt.type === "text-delta" && typeof evt.delta === "string") {
            assistantText += evt.delta;
            parsedSoFar += parser.parse(assistantId, assistantText);
            streamPending.current = { id: assistantId, text: parsedSoFar };
            scheduleFlush();
          }
        }
      }

      // Cancel any in-flight rAF and do a direct final commit so the
      // last batch of tokens is always visible.
      if (rafId.current !== null) {
        cancelAnimationFrame(rafId.current);
        rafId.current = null;
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
      streamPending.current = null;

      setStatus("ready");
      qc.invalidateQueries({ queryKey: ["conversations"] });
      if (chatId) qc.invalidateQueries({ queryKey: ["conversation", chatId] });
    } catch (e) {
      const err = e as Error & { name?: string };
      if (err?.name === "AbortError") {
        // User clicked Stop — keep whatever assistantText we got.
        setStatus("ready");
      } else {
        toast.error(err?.message ?? "Couldn't get a reply.");
        setStatus("error");
        setMessages((prev) => prev.filter((m) => m.id !== assistantId));
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
    setInput(prefillPrompt.text);
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
        placeholder={composerPlaceholder}
      />
    </div>
  );
}
