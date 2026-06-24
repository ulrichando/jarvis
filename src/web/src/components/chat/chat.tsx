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
import { PanelLeftOpen, Package } from "lucide-react";
import { Thread } from "./thread";
import { Composer } from "./composer";
import { EmptyState } from "./empty-state";
import { ScrollToBottomPill } from "./scroll-to-bottom-pill";
import { ShortcutsHelp } from "./shortcuts-help";
import { useStickToBottom } from "@/hooks/use-stick-to-bottom";
import { useKeyboardShortcuts } from "@/hooks/use-keyboard-shortcuts";
import { useRouter } from "next/navigation";
import { FunctionGrid } from "./function-grid";
import { TaskPanel } from "./task-panel";
import { useChatStore } from "@/stores/chat";
import { useVoiceMode } from "@/lib/chat/use-voice-mode";
import { appendImageMarkdown } from "@/lib/chat/image-markdown";
import { useEditedFiles } from "@/stores/edited-files";
import { useSettings } from "@/hooks/use-settings";
import { useSkills, expandSkill } from "@/hooks/use-skills";
import { useUI } from "@/stores/ui";
import { DEFAULT_MODEL, MODELS_META, modelSupportsVision } from "@/lib/ai/models-meta";
import { getProviderUX } from "@/lib/ai/provider-ux";
import { StreamingMessageParser } from "@/lib/actions/message-parser";
import { ActionRunner, type ActionEvent } from "@/lib/actions/runner";
import { apiWriteFile } from "@/lib/workspace/client";
import type { TrackedAction, ArtifactData, ArtifactKind } from "@/lib/actions/types";
import { ArtifactPanel } from "./artifact-panel";
import {
  ArtifactSidePanel,
  type PanelArtifact,
} from "@/components/artifacts/artifact-side-panel";
import { ArtifactChatCard } from "@/components/artifacts/artifact-chat-card";
import { useConversationArtifacts } from "@/hooks/use-artifacts";
import { ScaffoldPicker } from "@/components/workbench/scaffold-picker";

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

// VerifyOutcome shape now lives in @/lib/verify/types so client
// components (VerifyPill, Thread, Message) can share it.
import type { VerifyOutcome } from "@/lib/verify/types";

// Renders a synthetic block summarizing the verify pass — gets appended
// to the assistant message text just like <boltActionResults>, so the
// next turn's request includes the actual tsc / curl output.
function renderVerifyBlock(v: VerifyOutcome): string {
  const lines: string[] = [];
  lines.push(`<jarvisVerify ok="${v.ok}" durationMs="${v.durationMs}">`);
  if (v.fixers.length > 0) {
    lines.push("  <autoFixes>");
    for (const f of v.fixers) {
      lines.push(
        `    <fix rule="${f.rule}" files="${f.filesChanged.length}">${f.description}</fix>`,
      );
    }
    lines.push("  </autoFixes>");
  }
  if (v.typecheck.ran) {
    lines.push(
      `  <typecheck ok="${v.typecheck.ok}">${v.typecheck.output.trim() || "(no output)"}</typecheck>`,
    );
  }
  if (v.preview.ran) {
    lines.push(
      `  <preview ok="${v.preview.ok}" status="${v.preview.status ?? "n/a"}"/>`,
    );
  }
  if (v.screenshot) {
    // Don't dump the full data URL into the text body — would blow
    // context. Just record that a screenshot exists; the chat layer
    // attaches it as an image PART on the next turn so the multimodal
    // model can SEE the rendered output.
    lines.push(
      `  <screenshot bytes="${v.screenshot.bytes}" target="${v.screenshot.target}"/>`,
    );
  }
  lines.push("</jarvisVerify>");
  return lines.join("\n");
}

// ARCHITECTURE NOTE: synthetic auto-retry prompts have been REMOVED.
// Previous versions of this file rendered four kinds of fake user
// messages — verify-failed, real-shell-failure, diagnose-only, and
// thought-only — and fed them back to the model via a microtask
// to force an autonomous fix loop. That's the Cursor / Devin / Cline
// pattern, and it produced the failure modes the user kept hitting:
// transient curl-can't-connect classified as a real failure, retry
// prompts that demanded "complete file contents" (interpreted as
// "rewrite the whole project"), and a chat surface polluted with
// `[auto-retry]` text the user never typed. We now follow the Claude
// Code pattern instead: verify still runs, results are still appended
// to the assistant's text as a `<jarvisVerify>` block (model sees
// them on the next turn as ground truth), and the user sees a
// VerifyPill on the assistant message they can click to manually
// retry. No synthetic prompts; the model's own reasoning decides
// what to do.

// Diagnostic / read-only commands that can legitimately exit non-zero
// without the model considering it a failure. Currently unused after
// the auto-retry removal but kept here in case we want it for the
// VerifyPill's "should this even surface as a failure" classification.
const DIAGNOSTIC_PREFIXES = [
  "grep",
  "rg",
  "ag",
  "ack",
  "find",
  "test",
  "[",
  "[[",
  "diff",
  "pgrep",
  "head",
  "tail",
  "wc",
  "cat",
  "ls",
  "stat",
  "which",
  "type",
  "echo",
  "true",
  "false",
];

function isDiagnosticCommand(rawCommand: string): boolean {
  // Strip env-var prefixes (FOO=bar baz) and `cd … &&` shells, then
  // grab the head token. We only care about the FIRST real binary —
  // chained pipelines after that don't matter for the heuristic.
  let cmd = rawCommand.trim();
  // strip leading `cd ... &&` (can repeat)
  cmd = cmd.replace(/^(?:cd\s+\S+\s*&&\s*)+/, "");
  // strip leading env vars (e.g. PORT=5173 HOST=0.0.0.0 cmd)
  cmd = cmd.replace(/^(?:[A-Z_][A-Z0-9_]*=\S+\s+)+/, "");
  const head = cmd.split(/\s+|\||&|;/)[0]?.trim() ?? "";
  return DIAGNOSTIC_PREFIXES.includes(head);
}

// Detects a `<jarvisPlan stages="N">` declaration in the assistant
// text and returns N if found. Used by the chat layer to track
// long-horizon multi-stage builds (Replit Agent / Devin pattern).
// Did the assistant emit a `<boltArtifact>` or `<boltAction>` opening
// tag without a matching closer? Happens when the stream ends
// mid-tag — sometimes provider reports finishReason="stop" but the
// content is clearly incomplete (the closing tag is missing). Used
// by the auto-continue trigger to recover the same way it does on
// an explicit length finish: "continue exactly where you stopped"
// gets the model to emit the closer.
function hasOpenArtifactTag(text: string): boolean {
  // Lowercase + count opens vs closes for each tag. Conservative: a
  // tag is considered "open" only when there are MORE opens than
  // closes; equal counts means everything balanced even if the order
  // is weird.
  const lower = text.toLowerCase();
  const opens = (s: string) => (lower.match(new RegExp(`<${s}\\b`, "g")) || []).length;
  const closes = (s: string) => (lower.match(new RegExp(`</${s}\\s*>`, "g")) || []).length;
  return (
    opens("boltartifact") > closes("boltartifact") ||
    opens("boltaction") > closes("boltaction")
  );
}

function detectStageCount(assistantText: string): number | null {
  const m = assistantText.match(/<jarvisplan\b[^>]*\bstages\s*=\s*["'](\d+)["']/i);
  if (!m) return null;
  const n = parseInt(m[1], 10);
  if (!Number.isFinite(n) || n < 2 || n > 5) return null;
  return n;
}

// Continuation prompt the runtime auto-fires after a successful stage
// completes — moves the model on to the next stage without needing the
// user to type "continue".
function renderStageProgressPrompt(
  totalStages: number,
  nextStage: number,
): string {
  const lines: string[] = [];
  lines.push(
    `[auto-progress to stage ${nextStage} of ${totalStages}] Stage ${nextStage - 1} verified green. Now implement stage ${nextStage} from your earlier plan.`,
  );
  lines.push("");
  lines.push("Rules for this stage:");
  lines.push(
    `1. Re-state the goal of stage ${nextStage} in one sentence at the top of your response.`,
  );
  lines.push(
    "2. Emit ONE boltArtifact containing only the files this stage needs (don't re-write the previous stage's files).",
  );
  lines.push(
    "3. End with a verification shell action specific to this stage (curl an endpoint, query the db, run tsc).",
  );
  if (nextStage === totalStages) {
    lines.push(
      `4. This is the FINAL stage — after verification passes the build is done. Don't emit a "<jarvisPlan stages=...">" tag.`,
    );
  } else {
    lines.push(
      `4. The runtime will auto-fire stage ${nextStage + 1} after this verifies. Don't try to do later stages now.`,
    );
  }
  return lines.join("\n");
}

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
  // Artifact cards keyed by artifact id. `messageId` records the
  // assistant turn that produced each card — Thread uses this to render
  // each artifact INLINE under the message that created it, instead of
  // dumping every conversation-wide artifact into one bottom panel
  // (which made later turns appear to inherit older turns' artifacts).
  const [artifacts, setArtifacts] = useState<
    Map<string, { artifact: ArtifactData; actions: TrackedAction[]; messageId: string }>
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
  // Per-assistant-message error state. When a stream fails (network
  // drop, auth, rate-limit, server error) we keep the partial message
  // visible (Claude/ChatGPT pattern — never blank a partial response)
  // and stash the failure here so the Message component can render an
  // inline "Response stopped — Retry" pill at the end of the text.
  // Keyed by assistantId.
  const [errorById, setErrorById] = useState<Map<string, string>>(
    () => new Map(),
  );
  // Per-assistant-message verify outcome. Populated when the runtime
  // ran the verify pipeline (tsc / curl / screenshot) after the turn.
  // The model already gets these results as a `<jarvisVerify>` block
  // appended to its text — this map is purely so the UI can render a
  // VerifyPill on the message, letting the user see + manually retry.
  const [verifyById, setVerifyById] = useState<Map<string, VerifyOutcome>>(
    () => new Map(),
  );
  // ── claude.ai-style self-contained artifacts (System B) ──────────────
  // Separate from the bolt `artifacts` map above. Keyed by slug; holds the
  // CURRENT streamed content per artifact this session. Historical versions
  // come from the persisted query and are merged in `panelArtifacts` below.
  const [liveArtifacts, setLiveArtifacts] = useState<
    Map<
      string,
      {
        slug: string;
        title: string;
        kind: ArtifactKind;
        language?: string | null;
        content: string;
        messageId: string;
      }
    >
  >(() => new Map());
  const liveArtifactsRef = useRef(liveArtifacts);
  useEffect(() => {
    liveArtifactsRef.current = liveArtifacts;
  }, [liveArtifacts]);
  const [artifactPanelOpen, setArtifactPanelOpen] = useState(false);
  const [activeArtifactSlug, setActiveArtifactSlug] = useState<string | null>(
    null,
  );
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
  const { data: skills } = useSkills();
  // Image generation is available when an OpenAI or Google key is configured —
  // independent of the chat model. Drives the composer's "Image" toggle.
  const imageAvailable = Boolean(
    (
      settings?.providers as
        | Record<string, { hasKey?: boolean } | undefined>
        | undefined
    )?.google?.hasKey ||
      (
        settings?.providers as
          | Record<string, { hasKey?: boolean } | undefined>
          | undefined
      )?.openai?.hasKey,
  );

  // Settings → Notifications → "Response completions". Fires a browser
  // notification when a reply finishes AND the tab is in the background (no
  // point notifying a focused tab). No-op unless the toggle is on and the
  // user granted permission (requested when they flip the toggle in Settings).
  const maybeNotifyComplete = () => {
    if (!settings?.notifications?.responseCompletions) return;
    if (typeof document === "undefined" || !document.hidden) return;
    if (typeof Notification === "undefined" || Notification.permission !== "granted")
      return;
    try {
      const n = new Notification("JARVIS", {
        body: "Your response is ready.",
        tag: "jarvis-response",
      });
      n.onclick = () => {
        window.focus();
        n.close();
      };
    } catch {
      /* notification API can throw in some embedded contexts — ignore */
    }
  };

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
  // Fires once: after the first server-assigned conversation id on a
  // brand-new standalone /chat, replaceState the URL to /chat/<id> so a
  // refresh keeps the thread. Guarded so the auto-continue loop and
  // follow-up turns don't repeat it.
  const didSyncUrlRef = useRef(false);
  // Scroll container for the message thread. The useStickToBottom
  // hook tracks how close the user is to the bottom; isAtBottom
  // gates Thread's auto-scroll on stream so the page doesn't yank
  // the user back when they've scrolled up to read history. The
  // floating "Scroll to latest" pill renders inside the same
  // container with one-click re-attach. Matches Claude.ai /
  // ChatGPT scroll-stickiness UX.
  const scrollContainerRef = useRef<HTMLDivElement | null>(null);
  const { isAtBottom, scrollToBottom } = useStickToBottom(scrollContainerRef);
  // Cmd/Ctrl+/ help modal state. Wired by useKeyboardShortcuts below.
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  const router = useRouter();
  // rAF-based streaming flush: the hot loop writes here; a scheduled
  // animation-frame reads it and calls setMessages once per frame.
  // This never blocks the event loop (unlike flushSync).
  const streamPending = useRef<{ id: string; text: string } | null>(null);
  const rafId = useRef<number | null>(null);
  // Same pattern for the reasoning-delta stream — separate ref + rAF
  // slot so reasoning and text don't fight for the same frame.
  const reasoningPending = useRef<{ id: string; text: string } | null>(null);
  const reasoningRafId = useRef<number | null>(null);
  // Multi-stage plan progression state. When the model emits a plan with
  // `stages="N"`, the runtime auto-fires `[auto-progress to stage K]`
  // continuation turns until all stages complete or one fails. This is
  // what Replit Agent / Devin do for long-horizon builds — without it,
  // big projects hit token caps mid-artifact and stall.
  const stagePlanRef = useRef<{
    totalStages: number;
    currentStage: number; // 1-indexed; 1 = just-completed first stage
    stagesText: string; // raw plan body for re-injection on continuation prompts
  } | null>(null);
  // Most-recent live-preview screenshot from the verify pass. Captured
  // server-side via headless Chromium and stashed here so the NEXT turn
  // (auto-retry OR user-initiated) attaches it as an image part. Lets
  // multimodal models actually SEE the rendered output instead of
  // hallucinating "matches the design" — production tools (v0, Bolt,
  // Lovable) all loop screenshots back this way.
  const lastScreenshotRef = useRef<{
    dataUrl: string;
    target: string;
  } | null>(null);
  // Per-message usage data (input + output token counts + chosen model)
  // forwarded by the server via `messageMetadata` on the AI SDK stream.
  // Powers the small token-counter chip below each assistant message —
  // visibility into cost + budget control like Cursor / OpenRouter show.
  const [usageById, setUsageById] = useState<
    Map<
      string,
      {
        inputTokens: number;
        outputTokens: number;
        reasoningTokens?: number;
        model?: string;
      }
    >
  >(() => new Map());

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

  // Global keyboard shortcuts: Esc/Shift+Esc/Cmd+Shift+O/Cmd+/.
  // Esc gates on streaming so it doesn't steal Esc from popovers when
  // the model is idle. New chat resets to /chat (the route handles
  // wiping conversation state on mount via the URL change).
  useKeyboardShortcuts({
    isStreaming: status === "streaming" || status === "submitted",
    onStop: stop,
    onNewChat: () => router.push("/chat"),
    onToggleHelp: () => setShortcutsOpen((v) => !v),
  });

  // Per-turn DeepSeek toggle state (DeepThink → reasoner model; Search → gate
  // the web-search tool), read in the /api/chat request body below.
  const turnTogglesRef = useRef<{
    deepthink?: boolean;
    search?: boolean;
    image?: boolean;
  }>({});

  const submit = async (
    text?: string,
    opts: {
      isAutoRetry?: boolean;
      // Image attachments from the composer (data URLs). Embedded as
      // file parts in the user message so multimodal models receive
      // them inline. Non-multimodal models will silently ignore.
      images?: { id: string; dataUrl: string; name: string }[];
      // DeepSeek composer toggles for this turn (DeepThink / Search).
      toggles?: Record<string, boolean>;
    } = {},
  ) => {
    turnTogglesRef.current = {
      deepthink: opts.toggles?.deepthink,
      search: opts.toggles?.search,
      image: opts.toggles?.image,
    };
    // Expand a `/skill-name [args]` composer command into its saved prompt
    // template (Settings → Skills). No-op for non-skill text / while loading.
    const content = expandSkill((text ?? input).trim(), skills ?? []);
    const hasImages = (opts.images?.length ?? 0) > 0;
    if (
      (!content && !hasImages) ||
      status === "streaming" ||
      status === "submitted"
    ) {
      return;
    }
    // Warn (don't block) when an image is attached to a text-only model — the
    // attachment pipeline works, but a non-vision model just replies "I don't
    // see a picture", which is otherwise baffling.
    if (hasImages && !modelSupportsVision(model ?? DEFAULT_MODEL)) {
      toast.warning(
        "This model can't see images — switch to a vision model (Claude, GPT, or Gemini) to use the attachment.",
      );
    }

    // Reset multi-stage plan tracking on user-initiated turns so a new
    // build doesn't inherit stage state from the previous one. Auto-retry
    // turns preserve the state since they're continuations.
    if (!opts.isAutoRetry) {
      stagePlanRef.current = null;
    }

    // Per-turn checkpoint: snapshot the workspace BEFORE the upcoming
    // turn so the user can undo if jarvis breaks something. Tagged with
    // the assistantId we're about to generate, so each assistant message
    // has an associated rollback point. Best-effort — failures don't
    // block the turn (snapshots are nice-to-have, not load-bearing).
    // Only fires for workspace-scoped turns (no point snapshotting a
    // regular chat) and only on user-initiated submits (auto-retries
    // are continuations of the same turn, not a new state to checkpoint).

    // 1. Optimistic append: BOTH user message AND empty assistant
    // placeholder, synchronously, before the fetch starts. The
    // user sees their message immediately + the thinking-dots on
    // the empty assistant bubble.
    // Image parts use AI SDK 6's `file` UIMessagePart with a data URL.
    // The mediaType comes from the data URL prefix, falling back to a
    // generic image/png if the URL is malformed.
    const imageParts = (opts.images ?? []).map((img) => {
      const m = img.dataUrl.match(/^data:([^;]+);/);
      const mediaType = m?.[1] ?? "image/png";
      return {
        type: "file" as const,
        mediaType,
        url: img.dataUrl,
      };
    });
    // If we have a live-preview screenshot from the previous turn's
    // verify pass, attach it too. The multimodal model gets to SEE
    // the rendered output and compare against any design references
    // already in its context. We consume + clear the ref so each
    // screenshot is only sent once.
    const carryScreenshot = lastScreenshotRef.current;
    if (carryScreenshot) {
      imageParts.push({
        type: "file" as const,
        mediaType: "image/png",
        url: carryScreenshot.dataUrl,
      });
      lastScreenshotRef.current = null;
    }
    const userMessage: UIMessage = {
      id: makeId("u"),
      role: "user",
      // text part FIRST, image parts after — multimodal models accept
      // either order but text-first is the more common convention and
      // makes the persisted preview render naturally.
      parts: [
        ...(content ? [{ type: "text" as const, text: content }] : []),
        ...imageParts,
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      ] as any,
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

    // Fire-and-forget checkpoint snapshot. Doesn't block the network
    // round-trip; if it fails, the turn proceeds without a rollback
    // point (better than refusing to submit).
    if (targetWorkspaceId && !opts.isAutoRetry) {
      const labelText = content.slice(0, 60).replace(/\s+/g, " ");
      void fetch(`/api/workspace/${targetWorkspaceId}/checkpoint`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          id: assistantId,
          label: labelText,
        }),
      }).catch((err) => {
        console.warn("[chat] checkpoint snapshot failed:", err);
      });
    }

    // 1b. Set up streaming-action plumbing for this turn. The parser
    // walks the assistant's text as it streams and the runner executes
    // each <boltAction> against the target workspace container. Both
    // are local to this submit() call so previous turns' state can't
    // leak in.
    const localArtifacts = new Map<
      string,
      { artifact: ArtifactData; actions: TrackedAction[]; messageId: string }
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
      mut: (prev: {
        artifact: ArtifactData;
        actions: TrackedAction[];
        messageId: string;
      }) => {
        artifact: ArtifactData;
        actions: TrackedAction[];
        messageId: string;
      },
    ) => {
      const prev = localArtifacts.get(artifactId);
      if (!prev) return;
      const next = mut(prev);
      localArtifacts.set(artifactId, next);
      scheduleArtifactFlush();
    };

    // System-B self-contained artifacts. Seed from the running session
    // state so a revision in a later turn appends a version (vs resetting).
    // rAF-coalesced like the bolt flush above (stream fires many/sec).
    const localJarvis = new Map(liveArtifactsRef.current);
    let jarvisFlushScheduled = false;
    const scheduleJarvisFlush = () => {
      if (jarvisFlushScheduled) return;
      jarvisFlushScheduled = true;
      requestAnimationFrame(() => {
        jarvisFlushScheduled = false;
        setLiveArtifacts(new Map(localJarvis));
      });
    };

    const invalidateForFile = (filePath: string) => {
      qc.invalidateQueries({ queryKey: ["ws", targetWorkspaceId, "tree"] });
      qc.invalidateQueries({ queryKey: ["design-tree", targetWorkspaceId] });
      qc.invalidateQueries({
        queryKey: ["design-file", targetWorkspaceId, filePath],
      });
      // Mark the file as recently edited so the FileTree can paint a
      // "just edited" indicator. Markers fade out after 60s on their
      // own; no cleanup needed.
      if (targetWorkspaceId) {
        useEditedFiles.getState().markEdited(targetWorkspaceId, filePath);
      }
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
        localArtifacts.set(a.id, {
          artifact,
          actions: [],
          messageId: a.messageId,
        });
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
      onJarvisArtifactOpen: (a) => {
        // New emission of this slug (fresh artifact, or a revision in a
        // later turn) — reset the streamed content for the new version and
        // surface the panel focused on it.
        localJarvis.set(a.slug, {
          slug: a.slug,
          title: a.title,
          kind: a.kind,
          language: a.language,
          content: "",
          messageId: a.messageId,
        });
        setActiveArtifactSlug(a.slug);
        setArtifactPanelOpen(true);
        scheduleJarvisFlush();
      },
      onJarvisArtifactStream: (a) => {
        localJarvis.set(a.slug, {
          slug: a.slug,
          title: a.title,
          kind: a.kind,
          language: a.language,
          content: a.content,
          messageId: a.messageId,
        });
        scheduleJarvisFlush();
      },
      onJarvisArtifactClose: (a) => {
        localJarvis.set(a.slug, {
          slug: a.slug,
          title: a.title,
          kind: a.kind,
          language: a.language,
          content: a.content,
          messageId: a.messageId,
        });
        scheduleJarvisFlush();
      },
    });

    // 2. Network round-trip
    const ctrl = new AbortController();
    abortRef.current = ctrl;

    let assistantText = "";
    let parsedSoFar = "";
    // Images produced by the `generateImage` tool this turn. The custom SSE
    // parser is text-only, so we collect them from `tool-output-available`
    // events and append them as markdown at `finish` (after the text, matching
    // the server's onFinish persistence). Rendered via the Markdown <img>.
    const pendingImages: { url: string; prompt?: string }[] = [];
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

    // Conversation id for THIS submit. Starts as the prop, but once the first
    // turn returns X-Conversation-Id we pin it locally so auto-continue turns
    // extend the SAME conversation. Without this, a new chat (chatId starts
    // undefined) re-sent id:undefined on the continuation → a second
    // "Continue your previous output" chat appeared (the "two chats" bug).
    let convId = chatId;

    // One pass through fetch + stream-consume. Returns whether the stream
    // ended with finishReason="length" (= the model hit its token cap and
    // we should fire a continuation). Returns null on hard error / abort,
    // letting the caller bail without an auto-continue retry.
    const runOneStream = async (
      messagesForRequest: UIMessage[],
    ): Promise<"length" | "complete" | "error" | "abort"> => {
      // Watchdog: if the server doesn't return ANY response within 60s,
      // something's wrong (provider rejected, route hung, network dead).
      // Surface a toast so the user isn't just staring at the spinner.
      // 60s (was 20s) accommodates legitimately slow paths like K2.6
      // Swarm's decompose+fan-out before the response opens. Below 60s
      // the user should be staring at active progress already.
      const stuck = setTimeout(() => {
        toast.warning("Server hasn't responded in 60s", {
          description:
            "The provider may be slow or rejecting the request. Click Stop and try a smaller brief or a different model.",
          duration: 8000,
        });
      }, 60000);
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          id: convId,
          // DeepThink → DeepSeek's reasoning model (the exa-deepseek-chat pattern).
          model:
            turnTogglesRef.current.deepthink && model.startsWith("deepseek")
              ? "deepseek-reasoner"
              : model,
          messages: messagesForRequest,
          workspaceId: targetWorkspaceId ?? undefined,
          mode,
          format,
          // Search → gate the existing webSearchTool in the route.
          search: turnTogglesRef.current.search,
          // Image toggle → force server-side image generation (model-independent;
          // works with DeepSeek thinking models that can't reliably call tools).
          image: turnTogglesRef.current.image,
        }),
        signal: ctrl.signal,
      }).finally(() => clearTimeout(stuck));

      // Capture the server-assigned conversation ID so the parent can
      // persist it (per-workspace) and reload on refresh.
      const cid = res.headers.get("X-Conversation-Id");
      if (cid) {
        convId = cid; // pin so auto-continue turns extend THIS conversation
        if (onConversationId) onConversationId(cid);
      }
      // Standalone /chat: sync the URL to /chat/<id> after the first
      // message so refresh / back / share keep the open thread. Uses
      // replaceState (not a Next navigation) so the in-flight stream
      // isn't remounted; a later refresh hits the /chat/[id] server route
      // and rehydrates from the DB. Gated on pathname === "/chat" so it
      // never fires for the embedded composers (workbench, design) or an
      // already-id'd /chat/[id] thread.
      if (
        cid &&
        !didSyncUrlRef.current &&
        typeof window !== "undefined" &&
        window.location.pathname === "/chat"
      ) {
        didSyncUrlRef.current = true;
        window.history.replaceState(null, "", `/chat/${cid}`);
      }

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
            // finally arrives. We DO call parse() for its callback side
            // effects (artifact panel updates), but we no longer use
            // its return value for the rendered message — instead the
            // Markdown component sanitizes the raw text at display
            // time. Storing raw text in `messages` state means the next
            // turn's request body carries the full <boltArtifact>
            // context (Bolt-style), so the model sees what it wrote.
            parser.parse(assistantId, assistantText);
            parsedSoFar = assistantText;
            streamPending.current = { id: assistantId, text: assistantText };
            // Settings → Capabilities → Streaming. When off, skip the live
            // per-token flush — text accumulates and is committed once at
            // finalize, so the reply appears all at once. Default on.
            if (settings?.capabilities?.streaming !== false) scheduleFlush();
          } else if (
            evt.type === "reasoning-delta" &&
            typeof evt.delta === "string"
          ) {
            reasoningText += evt.delta;
            reasoningPending.current = { id: assistantId, text: reasoningText };
            scheduleReasoningFlush();
          } else if (evt.type === "tool-output-available") {
            // generateImage tool result. Collect successful images; append
            // them at `finish` (below) so they land AFTER the model's text,
            // matching the server's persisted order. Errors flow through the
            // model's own text (it sees the error in the tool output).
            const out = (
              evt as unknown as {
                output?: { status?: string; url?: string; prompt?: string };
              }
            ).output;
            if (out?.status === "ok" && typeof out.url === "string") {
              pendingImages.push({ url: out.url, prompt: out.prompt });
            }
          } else if (evt.type === "finish") {
            finishReason = (evt as unknown as { finishReason?: string })
              .finishReason;
            // Reconcile generated images. appendImageMarkdown ALSO strips any
            // /api/media markdown the model wrote in its own text — including a
            // HALLUCINATED fake url (no tool call) that would otherwise render
            // as a broken <img>. So run it unconditionally: it appends the real
            // tool images AND removes model-emitted ones. Same helper the server
            // uses for persistence, so live + reload stay identical.
            const reconciled = appendImageMarkdown(assistantText, pendingImages);
            if (reconciled !== assistantText) {
              assistantText = reconciled;
              parsedSoFar = assistantText;
              streamPending.current = { id: assistantId, text: assistantText };
              scheduleFlush();
            }
          } else if (evt.type === "message-metadata") {
            // Server-forwarded per-step usage. We accumulate input +
            // output across multi-step turns (auto-continuations etc.)
            // so the chip reflects total cost of the assistant message.
            const meta = (evt as unknown as {
              messageMetadata?: {
                usage?: {
                  inputTokens?: number;
                  outputTokens?: number;
                  reasoningTokens?: number;
                };
                model?: string;
              };
            }).messageMetadata;
            if (meta?.usage) {
              const incoming = meta.usage;
              setUsageById((prev) => {
                const next = new Map(prev);
                const cur = next.get(assistantId);
                next.set(assistantId, {
                  inputTokens:
                    (cur?.inputTokens ?? 0) + (incoming.inputTokens ?? 0),
                  outputTokens:
                    (cur?.outputTokens ?? 0) + (incoming.outputTokens ?? 0),
                  reasoningTokens:
                    (cur?.reasoningTokens ?? 0) +
                    (incoming.reasoningTokens ?? 0),
                  model: meta.model ?? cur?.model,
                });
                return next;
              });
            }
          }
        }
      }
      // Truncation detection: even when the provider reports
      // finishReason="stop" (not "length"), the assistant text can
      // end mid-`<boltAction>` or mid-`<boltArtifact>` — the model
      // ran out of internal budget but didn't surface it as a length
      // signal. Treat that as a length finish so auto-continue can
      // close the artifact properly. This is a CONTINUATION of the
      // same turn ("finish what you started"), not a synthetic
      // retry — Cursor / Bolt / Claude Code all do this.
      if (finishReason !== "length" && hasOpenArtifactTag(assistantText)) {
        return "length";
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
            // KEEP the partial message visible (Claude/ChatGPT pattern
            // — never blank a partial response) and stash the failure
            // so the Message component renders an inline retry pill.
            // Empty-text placeholders still get removed (a "thinking…"
            // bubble with no body and no error is just visual noise).
            const hasContent = assistantText.trim().length > 0;
            if (hasContent) {
              setErrorById((prev) =>
                new Map(prev).set(
                  assistantId,
                  "Response stopped",
                ),
              );
            } else {
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
      // Final commit uses RAW model text (assistantText) so the message
      // state matches exactly what the server saved + what the next
      // turn's request will send. The Markdown component sanitizes
      // <boltArtifact>/<jarvisPlan>/<boltActionResults> tags at render
      // time so the visible body stays clean.
      if (assistantText) {
        const finalText = assistantText;
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

        // Verification-as-gate: after the turn's actions drain, run
        // the workspace's rule-based fixers + tsc + curl. We don't
        // trust the model's "✅ verified" claim — we verify ourselves.
        // If anything fails, the result feeds into the auto-retry
        // decision below so the next turn's prompt has the actual
        // error. Fixers run first and may auto-resolve common bugs
        // without spending an LLM turn at all.
        let verifyResult: VerifyOutcome | null = null;
        if (targetWorkspaceId) {
          // Only verify if this turn produced file edits — running tsc
          // on every diagnose-only turn wastes ~10s of dev time.
          let hadFileEditsForVerify = false;
          for (const card of localArtifacts.values()) {
            if (card.actions.some((a) => a.action.type === "file")) {
              hadFileEditsForVerify = true;
              break;
            }
          }
          if (hadFileEditsForVerify) {
            // Auto-commit BEFORE verify. Capturing the model's output as
            // a real git commit even when verify fails means we always
            // have a recoverable point — and the commit history mirrors
            // the chat history one-to-one. Fire-and-forget; commit
            // failures don't block the turn.
            fetch(`/api/workspace/${targetWorkspaceId}/commit`, {
              method: "POST",
              headers: { "content-type": "application/json" },
              body: JSON.stringify({
                message: content.slice(0, 200) || "update",
              }),
            }).catch((err) => {
              console.warn("[chat] auto-commit failed:", err);
            });
            try {
              const r = await fetch(
                `/api/workspace/${targetWorkspaceId}/verify`,
                { method: "POST" },
              );
              if (r.ok) {
                verifyResult = (await r.json()) as VerifyOutcome;
                // Stash the verify outcome on a per-message map so the
                // UI can render a VerifyPill below the assistant body.
                // This is the user-facing surface that REPLACES the
                // synthetic [auto-retry] prompts — verify outcome is
                // now visible as a clickable pill, not as a fake user
                // message in the chat.
                if (verifyResult) {
                  const outcome = verifyResult;
                  setVerifyById((prev) =>
                    new Map(prev).set(assistantId, outcome),
                  );
                }
                // Stash the live-preview screenshot for the NEXT turn
                // to attach as an image part. The model on the next turn
                // can compare it to design references in its context.
                if (verifyResult?.screenshot) {
                  lastScreenshotRef.current = {
                    dataUrl: verifyResult.screenshot.dataUrl,
                    target: verifyResult.screenshot.target,
                  };
                }
                // Append a synthetic verification block to the message
                // text so the next turn's request includes the actual
                // tsc / curl output — same channel as boltActionResults.
                if (verifyResult) {
                  const block = renderVerifyBlock(verifyResult);
                  assistantText += `\n\n${block}\n`;
                  startTransition(() => {
                    setMessages((prev) =>
                      prev.map((m) =>
                        m.id !== assistantId
                          ? m
                          : {
                              ...m,
                              parts: [
                                {
                                  type: "text",
                                  text: `${m.parts
                                    .map((p) =>
                                      p.type === "text" ? p.text : "",
                                    )
                                    .join("")}\n\n${block}\n`,
                                } as never,
                              ],
                            },
                      ),
                    );
                  });
                }
              }
            } catch (err) {
              console.warn("[chat] verify call failed:", err);
            }
          }
        }

        // Auto-retry-on-failure (Bolt / Replit / Lovable pattern). Only
        // fires when the user submitted (NOT when this submit IS the
        // retry — caps the chain at 1 auto-retry per user prompt) AND
        // there's a real failure to react to. The retry is a synthetic
        // user message rendered by renderAutoRetryPrompt(); the model
        // sees the previous boltActionResults block in its history and
        // decides whether to fix or escalate. Diagnostic commands
        // (grep/find/cat returning non-zero on "no match") are excluded
        // so we don't kick a retry over a successful exploratory probe.
        // Stage-progression bookkeeping for multi-stage plans. We
        // intentionally do NOT inject auto-retry prompts on verify
        // failure or diagnose-only turns — see the comment block
        // below for why. Stage progression is the only autonomous
        // turn-firing path that survives.
        if (!opts.isAutoRetry && targetWorkspaceId) {
          let hadFileEdits = false;
          for (const card of localArtifacts.values()) {
            if (card.actions.some((a) => a.action.type === "file")) {
              hadFileEdits = true;
              break;
            }
          }
          const verifyFailed = !!verifyResult && verifyResult.ok === false;
          // Multi-stage plan detection. When the model emits a
          // `<jarvisPlan stages="N">` on the first turn, capture N.
          // Subsequent stage progression is tracked via
          // stagePlanRef.current.currentStage.
          if (!opts.isAutoRetry) {
            const detected = detectStageCount(assistantText);
            if (detected) {
              stagePlanRef.current = {
                totalStages: detected,
                currentStage: 1,
                stagesText: assistantText.match(
                  /<jarvisplan\b[^>]*>([\s\S]*?)<\/jarvisplan>/i,
                )?.[1] ?? "",
              };
            }
          }
          // Auto-retry-on-failure REMOVED — see ARCHITECTURE NOTE below.
          //
          // The previous implementation injected synthetic [auto-retry] /
          // [diagnose-only] / [thought-only] user messages into the chat
          // to force the model into a fix loop — the Cursor/Devin/Cline
          // pattern. Concrete failure modes that drove the removal:
          //   - Transient curl status 0 (dev server not up yet) read as
          //     a real failure → spurious retry → rewrite-everything loop
          //   - Retry prompts told the model "Complete file contents —
          //     never diffs", which it interpreted as "re-emit the whole
          //     project", producing the file-drift bug the user kept
          //     hitting
          //   - User saw raw "[auto-retry]" prompts in their chat surface
          //     even though they hadn't typed anything — confusing UX
          //
          // We're now using the Claude Code pattern: the verify pipeline
          // still runs and its results are appended to the assistant
          // text as a `<jarvisVerify>` block (already happens above);
          // the model sees that block on the next user turn as ground
          // truth and reasons about whether to fix. The user sees a
          // VerifyPill on the assistant message so they can explicitly
          // click "Try fix" — no synthetic prompts, no spinning, no
          // transient-error misclassification.
          if (
            stagePlanRef.current &&
            stagePlanRef.current.currentStage <
              stagePlanRef.current.totalStages &&
            hadFileEdits &&
            !verifyFailed
          ) {
            // Stage-progression trigger. Previous stage finished cleanly
            // (had file edits + verify green + no shell failures) AND
            // we're tracking a multi-stage plan that has more stages to
            // go. Auto-fire the next stage's continuation prompt without
            // user intervention. This mirrors Replit Agent / Devin's
            // long-horizon execution model.
            const next = stagePlanRef.current.currentStage + 1;
            const total = stagePlanRef.current.totalStages;
            stagePlanRef.current.currentStage = next;
            const progressPrompt = renderStageProgressPrompt(total, next);
            queueMicrotask(() => {
              void submit(progressPrompt, { isAutoRetry: true });
            });
            // If this is the LAST stage, clear the ref so the next user
            // turn starts fresh (otherwise a new multi-stage plan would
            // collide with the leftover state).
            if (next >= total) {
              // Don't clear yet — clear AFTER the final stage's turn
              // actually completes. We'll do that in a final-stage hook
              // at the top of the next submit call (where opts.isAutoRetry
              // is true), or just leave it; either way subsequent
              // detectStageCount calls will reset it on a fresh
              // user-initiated turn.
            }
          }
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
        maybeNotifyComplete();
        qc.invalidateQueries({ queryKey: ["conversations"] });
        if (chatId) qc.invalidateQueries({ queryKey: ["conversation", chatId] });
        // Refetch persisted artifacts so the panel picks up DB ids +
        // canonical version history (enables publish/open-in-tab).
        if (chatId) {
          qc.invalidateQueries({
            queryKey: ["artifacts", "conversation", chatId],
          });
          qc.invalidateQueries({ queryKey: ["artifacts"] });
        }
      }
    } catch (e) {
      const err = e as Error & { name?: string };
      if (err?.name === "AbortError") {
        // User clicked Stop — keep whatever assistantText we got.
        setStatus("ready");
      } else {
        // Same Claude/ChatGPT pattern as the in-stream error path:
        // keep the partial message visible if we got any content
        // before the fetch threw, and surface the error inline so
        // the user can retry without losing the in-progress reply.
        // Toast still fires for screen-reader announcement.
        const errMsg = err?.message ?? "Couldn't get a reply.";
        toast.error(errMsg);
        setStatus("error");
        const hasContent = assistantText.trim().length > 0;
        if (hasContent) {
          setErrorById((prev) => new Map(prev).set(assistantId, errMsg));
        } else {
          setMessages((prev) => prev.filter((m) => m.id !== assistantId));
        }
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
      // Finalize any artifact actions still in queued/running state.
      // The runner only transitions to success/error when the parser
      // sees the closing </boltAction> tag — but if the stream cuts
      // off mid-action (model truncated, network blip, user hit Stop)
      // those actions stay "running" forever. The composer's Stop
      // button hides as soon as `status` flips to ready, so the user
      // sees an artifact card with "1 running" but no way to cancel.
      // Sweep terminal-state any non-terminal actions so the UI
      // reflects reality. Marking as "error" so the spinner stops
      // and the card surfaces an "incomplete" state instead of
      // hanging forever.
      let anyStuck = false;
      for (const [id, card] of localArtifacts) {
        const fixed = card.actions.map((a) =>
          a.status === "queued" || a.status === "running"
            ? { ...a, status: "error" as const, error: "stream ended before action closed" }
            : a,
        );
        if (fixed.some((a, i) => a !== card.actions[i])) {
          anyStuck = true;
          localArtifacts.set(id, { ...card, actions: fixed });
        }
      }
      if (anyStuck) {
        setArtifacts(new Map(localArtifacts));
      }
    }
  };

  // Rebuild artifact + plan state from loaded history. The DB stores the
  // raw assistant text including <boltArtifact>/<jarvisPlan> markers,
  // but the artifact panel + plan card are driven by per-message state
  // (artifacts Map, planById Map) that the streaming parser populates
  // in real-time. On refresh those Maps are empty — so without this,
  // historical assistant messages render as a stripped prose body with
  // no file cards or plan card. Bolt-style: re-parse each historical
  // message and feed reconstruction-only callbacks (no runner, no live
  // file writes — actions are marked success since they already ran).
  const rehydratedRef = useRef(false);
  useEffect(() => {
    if (rehydratedRef.current) return;
    if (!initialMessages || initialMessages.length === 0) return;
    rehydratedRef.current = true;

    const reconstructionParser = new StreamingMessageParser({
      onPlan: (p) => {
        // Only land the final plan in state; mid-stream events from
        // historical reconstruction are noise.
        if (!p.complete) return;
        setPlanById((prev) => {
          const next = new Map(prev);
          next.set(p.messageId, {
            content: p.content,
            complete: true,
          });
          return next;
        });
      },
      onArtifactOpen: (a) => {
        setArtifacts((prev) => {
          const next = new Map(prev);
          next.set(a.id, {
            artifact: { id: a.id, title: a.title, type: a.type },
            actions: [],
            messageId: a.messageId,
          });
          return next;
        });
      },
      onActionClose: (a) => {
        // Each action is treated as already-executed (success). We don't
        // have the original exit code or stdout, but the artifact panel
        // only needs status="success" to render the green check.
        setArtifacts((prev) => {
          const existing = prev.get(a.artifactId);
          if (!existing) return prev;
          // Dedupe: if React 19 strict-mode double-invokes this effect
          // (refs get a fresh init on the second mount), the same
          // actionId could land twice and break the panel's keys. Skip
          // if already present.
          if (existing.actions.some((x) => x.actionId === a.actionId)) {
            return prev;
          }
          const tracked: TrackedAction = {
            artifactId: a.artifactId,
            actionId: a.actionId,
            action: a.action,
            status: "success",
          };
          const next = new Map(prev);
          next.set(a.artifactId, {
            ...existing,
            actions: [...existing.actions, tracked],
          });
          return next;
        });
      },
    });

    for (const m of initialMessages) {
      if (m.role !== "assistant") continue;
      const text = m.parts
        .map((p) => (p.type === "text" ? p.text : ""))
        .join("");
      if (!text) continue;
      reconstructionParser.parse(m.id, text);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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

  // ── Artifact panel data (System B) ──────────────────────────────────
  // Persisted artifacts for this conversation (DB id + version history).
  // Plain chat only — the embedded workbench chat uses System A.
  const { data: persistedArtifacts } = useConversationArtifacts(
    !embedded ? chatId : undefined,
  );
  // Merge persisted (authoritative: id, history, shareToken) with the live
  // session stream (a freshly streamed/edited version not yet persisted).
  const panelArtifacts = useMemo<PanelArtifact[]>(() => {
    const bySlug = new Map<string, PanelArtifact>();
    for (const p of persistedArtifacts ?? []) {
      bySlug.set(p.slug, {
        id: p.id,
        slug: p.slug,
        title: p.title,
        kind: p.kind,
        language: p.versions.at(-1)?.language ?? null,
        versions: p.versions.map((v) => v.content),
        shareToken: p.shareToken,
      });
    }
    for (const [slug, l] of liveArtifacts) {
      const existing = bySlug.get(slug);
      if (!existing) {
        if (!l.content) continue; // nothing streamed yet
        bySlug.set(slug, {
          slug,
          title: l.title,
          kind: l.kind,
          language: l.language ?? null,
          versions: [l.content],
          shareToken: null,
        });
        continue;
      }
      // A new/edited version streaming before it's persisted → show it live.
      if (l.content && l.content !== existing.versions.at(-1)) {
        bySlug.set(slug, {
          ...existing,
          title: l.title || existing.title,
          versions: [...existing.versions, l.content],
        });
      }
    }
    return [...bySlug.values()];
  }, [persistedArtifacts, liveArtifacts]);

  const showArtifactPanel =
    !embedded && artifactPanelOpen && panelArtifacts.length > 0;
  const showReopenPill =
    !embedded && !artifactPanelOpen && panelArtifacts.length > 0;

  // claude.ai-style inline cards: which artifacts each assistant turn
  // produced/updated, so Thread can render a clickable card under that
  // message (live this session, or from the persisted version's messageId).
  const jarvisCardsByMessage = useMemo(() => {
    const out = new Map<
      string,
      { slug: string; title: string; kind: ArtifactKind }[]
    >();
    const add = (
      mid: string | null | undefined,
      card: { slug: string; title: string; kind: ArtifactKind },
    ) => {
      if (!mid) return;
      const arr = out.get(mid) ?? [];
      if (!arr.some((c) => c.slug === card.slug)) arr.push(card);
      out.set(mid, arr);
    };
    for (const a of persistedArtifacts ?? [])
      add(a.versions.at(-1)?.messageId, {
        slug: a.slug,
        title: a.title,
        kind: a.kind,
      });
    for (const [, l] of liveArtifacts)
      add(l.messageId, { slug: l.slug, title: l.title, kind: l.kind });
    return out;
  }, [persistedArtifacts, liveArtifacts]);

  const renderJarvisCards = (messageId: string) => {
    const cards = jarvisCardsByMessage.get(messageId);
    if (!cards || cards.length === 0) return null;
    return cards.map((c) => (
      <ArtifactChatCard
        key={c.slug}
        title={c.title}
        kind={c.kind}
        onOpen={() => {
          setActiveArtifactSlug(c.slug);
          setArtifactPanelOpen(true);
        }}
      />
    ));
  };

  // Follow ASYNC height growth to the bottom while the user is stuck. Thread's
  // auto-scroll only fires on `messages` changes — so content that grows the
  // height WITHOUT a messages change (a generated <img> finishing its load, a
  // late embed, reasoning expanding) wouldn't pull the view down, which is the
  // "doesn't scroll after generating an image" bug. The stick latch stays true
  // through growth (it only disarms on a deliberate user scroll-up), so we only
  // follow when the user hasn't scrolled away to read history. Instant, not
  // smooth, to avoid stacking animations during rapid growth. Keyed on isEmpty
  // so the observer (re)binds when the first message mounts the container.
  const isAtBottomRef = useRef(isAtBottom);
  isAtBottomRef.current = isAtBottom;
  useEffect(() => {
    const el = scrollContainerRef.current;
    if (!el) return;
    let lastHeight = el.scrollHeight;
    const follow = () => {
      const h = el.scrollHeight;
      if (h > lastHeight + 1 && isAtBottomRef.current) {
        el.scrollTo({ top: h, behavior: "auto" });
      }
      lastHeight = h;
    };
    const ro = new ResizeObserver(follow);
    ro.observe(el);
    // Content is usually nested one level deep (overflow: visible on the inner
    // wrapper), so the container's own box may not resize when content grows.
    const inner = el.firstElementChild;
    if (inner) ro.observe(inner);
    return () => ro.disconnect();
  }, [isEmpty]);

  // When embedded in the workbench we always want the composer pinned
  // to the bottom (like a real chat) regardless of whether there are
  // messages yet. The standalone /chat empty state still uses the
  // Voice mode (live conversation): listen → submit each utterance → read the
  // reply aloud → resume. Isolated in useVoiceMode; here we just submit
  // transcripts and speak the assistant reply when a turn finishes.
  const voice = useVoiceMode({
    onUtterance: (t) => {
      void submit(t);
    },
  });
  const voiceActive = voice.active;
  const voiceSpeak = voice.speak;
  // Read the latest assistant reply aloud exactly once per turn. LEVEL-triggered
  // (status==="ready" + a per-message-id dedupe) rather than edge-detecting the
  // streaming→ready transition: this is a custom chat loop where the final text
  // chunk (setMessages) and the setStatus("ready") flip land in separate renders,
  // and the old effect didn't depend on `messages` — so whenever the edge wasn't
  // cleanly observed the reply was never spoken ("reads some replies, not others").
  // Depending on `messages` means a reply that finalizes a tick after the status
  // flip is still caught; the id guard prevents re-reads.
  const lastSpokenIdRef = useRef<string | null>(null);
  const voiceWasActiveRef = useRef(false);
  const voiceMessagesRef = useRef(messages);
  voiceMessagesRef.current = messages;
  useEffect(() => {
    // Enabling voice mode marks the current last reply as already-handled so
    // turning it on mid-conversation doesn't re-read history; disabling resets.
    if (voiceActive && !voiceWasActiveRef.current) {
      const msgs = voiceMessagesRef.current;
      let lastId: string | null = null;
      for (let i = msgs.length - 1; i >= 0; i--) {
        if (msgs[i].role === "assistant") {
          lastId = msgs[i].id;
          break;
        }
      }
      lastSpokenIdRef.current = lastId;
    } else if (!voiceActive && voiceWasActiveRef.current) {
      lastSpokenIdRef.current = null;
    }
    voiceWasActiveRef.current = voiceActive;
  }, [voiceActive]);
  useEffect(() => {
    if (!voiceActive || status !== "ready") return;
    let last: UIMessage | undefined;
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].role === "assistant") {
        last = messages[i];
        break;
      }
    }
    if (!last || last.id === lastSpokenIdRef.current) return;
    const text = last.parts
      .map((p) => (p.type === "text" ? p.text : ""))
      .join("")
      .trim();
    if (!text) return;
    lastSpokenIdRef.current = last.id;
    voiceSpeak(text, last.id);
  }, [status, voiceActive, voiceSpeak, messages]);

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
              onSubmit={(o) => submit(undefined, o)}
              onStop={stop}
              voicePhase={voice.phase}
              onToggleVoice={voice.toggle}
              status={status}
              provider={provider}
              hideWorkspacePicker={embedded}
              unifiedUX={unifiedUX}
              placeholder={composerPlaceholder}
              imageAvailable={imageAvailable && !targetWorkspaceId}
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
      <div
      className="flex h-full flex-col"
      data-chat-font={settings?.appearance?.fontSize ?? "md"}
      data-chat-density={settings?.appearance?.density ?? "cozy"}
    >
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
        <div className="flex-1 overflow-y-auto px-4 py-6 space-y-4">
          <div>
            <p className="text-[13px] text-muted-foreground">
              What do you want to build?
            </p>
            <p className="mt-1 text-[12px] text-muted-foreground/70">
              Files you ask for go straight into{" "}
              <span className="font-mono">{targetWorkspaceName ?? "this workspace"}</span>.
            </p>
          </div>
          {/* Scaffolds are workbench-only — they drop full-stack
              starters (package.json, configs, dev scripts). The Design
              tab is for single-file HTML/JSX mockups, where a scaffold
              would be confusing noise. */}
          {targetWorkspaceId && mode !== "design" && (
            <ScaffoldPicker workspaceId={targetWorkspaceId} />
          )}
        </div>
        <Composer
          value={input}
          onChange={setInput}
          onSubmit={(o) => submit(undefined, o)}
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
    <div
      className="relative flex h-full flex-col"
      data-chat-font={settings?.appearance?.fontSize ?? "md"}
      data-chat-density={settings?.appearance?.density ?? "cozy"}
    >
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
      <div className="flex min-h-0 flex-1">
        <div className="flex min-w-0 flex-1 flex-col">
      <div ref={scrollContainerRef} className="relative flex-1 overflow-y-auto">
        <Thread
          messages={messages}
          isStreaming={status === "streaming" || status === "submitted"}
          reasoningById={reasoningById}
          planById={planById}
          usageById={usageById}
          errorById={errorById}
          verifyById={verifyById}
          onVerifyRetry={(messageId) => {
            // Manual "Try fix" from the VerifyPill. Find the failed
            // assistant message + its preceding user prompt, then
            // submit a tight one-line message asking for a targeted
            // fix. The model's context already includes the
            // <jarvisVerify> block from the failed turn, so this is
            // just a nudge to act on what it can already see.
            const idx = messages.findIndex((m) => m.id === messageId);
            if (idx < 0) return;
            void submit(
              "The verify pipeline reported errors above. Read the <jarvisVerify> block from your last turn and fix the failing files — only the files that need changing.",
            );
          }}
          onRetry={(messageId) => {
            // Re-submit the user message that triggered this failed
            // assistant turn. Find the user message immediately
            // preceding the failed assistant message, extract its
            // text, clear the error, and call submit().
            const idx = messages.findIndex((m) => m.id === messageId);
            if (idx <= 0) return;
            // Walk back to the most recent user message before this
            // assistant — auto-continue continuations have an
            // assistant message immediately before, but the user's
            // ORIGINAL prompt is the prior user turn.
            let userIdx = idx - 1;
            while (userIdx >= 0 && messages[userIdx].role !== "user") {
              userIdx--;
            }
            if (userIdx < 0) return;
            const userText = messages[userIdx].parts
              .map((p) => (p.type === "text" ? p.text : ""))
              .join("");
            // Clear the error pill on the failed message so it stops
            // showing while the retry runs.
            setErrorById((prev) => {
              if (!prev.has(messageId)) return prev;
              const next = new Map(prev);
              next.delete(messageId);
              return next;
            });
            // Drop the failed assistant message (and any artifacts /
            // reasoning attached to it) so the retry produces a fresh
            // turn instead of appending to the broken one.
            setMessages((prev) => prev.filter((m) => m.id !== messageId));
            void submit(userText);
          }}
          workspaceId={targetWorkspaceId ?? undefined}
          isAtBottom={isAtBottom}
          artifacts={artifacts}
          renderArtifacts={(cards) => (
            <ArtifactPanel
              artifacts={cards}
              workspaceId={targetWorkspaceId}
              workspaceName={targetWorkspaceName}
              previewPort={previewPort}
              embedded={embedded}
            />
          )}
          renderJarvisCards={embedded ? undefined : renderJarvisCards}
        />
        <ScrollToBottomPill visible={!isAtBottom} onClick={scrollToBottom} />
      </div>
      <Composer
        value={input}
        onChange={setInput}
        onSubmit={(o) => submit(undefined, o)}
        onStop={stop}
        voicePhase={voice.phase}
        onToggleVoice={voice.toggle}
        status={status}
        provider={provider}
        hideWorkspacePicker={embedded}
        unifiedUX={unifiedUX}
        placeholder={composerPlaceholder}
        imageAvailable={imageAvailable && !targetWorkspaceId}
      />
        </div>
        {showArtifactPanel && (
          <div className="w-[44%] min-w-[380px] max-w-[680px] shrink-0 overflow-hidden border-l border-border/60">
            <ArtifactSidePanel
              artifacts={panelArtifacts}
              activeSlug={
                activeArtifactSlug ??
                panelArtifacts[panelArtifacts.length - 1]?.slug ??
                ""
              }
              onActiveSlugChange={setActiveArtifactSlug}
              onClose={() => setArtifactPanelOpen(false)}
            />
          </div>
        )}
      </div>
      {showReopenPill && (
        <button
          onClick={() => {
            setArtifactPanelOpen(true);
            setActiveArtifactSlug(
              panelArtifacts[panelArtifacts.length - 1]?.slug ?? null,
            );
          }}
          className="absolute bottom-24 right-5 z-20 flex items-center gap-1.5 rounded-full border border-border/60 bg-card/90 px-3 py-1.5 text-[12px] font-medium text-foreground shadow-md backdrop-blur transition-colors hover:bg-accent"
        >
          <Package className="size-3.5 text-muted-foreground" />
          Artifacts ({panelArtifacts.length})
        </button>
      )}
      <ShortcutsHelp open={shortcutsOpen} onOpenChange={setShortcutsOpen} />
    </div>
  );
}
