"use client";

import { useEffect, useRef, useState } from "react";
import { motion, AnimatePresence } from "motion/react";
import type { UIMessage } from "ai";
import {
  Check,
  Copy,
  ThumbsUp,
  ThumbsDown,
  RotateCcw,
  ChevronDown,
  Brain,
  ListChecks,
  Undo2,
  Loader2,
  Hammer,
  Download,
  AlertTriangle,
  Image as ImageIcon,
  type LucideIcon,
} from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Markdown } from "@/components/markdown/markdown";
import { Sources, extractSources } from "./sources";
import { KimiReasoning } from "./kimi-reasoning";
import { KimiToolTrace, type ToolTraceEntry } from "./kimi-tool-trace";
import { KimiSwarmProgress } from "./kimi-swarm-progress";
import { cn } from "@/lib/utils";
import { MODELS_META } from "@/lib/ai/models-meta";
import { useVoiceRead } from "@/stores/voice-read";

// Synthetic-prompt patterns the chat layer's plumbing emits but the
// user shouldn't see. Stripped at the SOURCE (textFromParts) so every
// render path — assistant <Markdown />, user <p>{text}</p>, accessible
// labels, copy-to-clipboard — gets clean text in one place. Multiple
// patterns because models sometimes echo only the tail of the prompt
// back, leaving leading-only or trailing-only fragments in history.
const SYNTHETIC_PROMPT_PATTERNS: RegExp[] = [
  // Full auto-continue prompt (canonical form)
  /Continue your previous output exactly where you stopped[\s\S]*?Close any open boltAction[^.]*\.?/g,
  // Just the tail when the model echoed only the closing instruction
  /Close any open boltAction and the boltArtifact properly\.?/g,
  // Just the leading sentence when the model echoed only the opener
  /Continue your previous output exactly where you stopped\.?(?:\s+Do NOT[^.]*\.?)*/g,
  // Stray "boltArtifact properly." at start of a line — last-resort
  // for the case where everything else got truncated and only this
  // sentence-end fragment remains.
  /^[ \t]*boltArtifact properly\.?[ \t]*$/gm,
];

function cleanSyntheticPrompts(text: string): string {
  let out = text;
  for (const re of SYNTHETIC_PROMPT_PATTERNS) {
    out = out.replace(re, "");
  }
  // Collapse the runs of blank lines the strips leave behind.
  return out.replace(/\n{3,}/g, "\n\n").trim();
}

function textFromParts(parts: UIMessage["parts"]): string {
  const raw = parts.map((p) => (p.type === "text" ? p.text : "")).join("");
  return cleanSyntheticPrompts(raw);
}

// Wrap inline `[N]` references with a markdown link to the matching
// source URL, so Markdown's `<a>` renderer (which detects citation
// links and styles them as superscript chips) picks them up. Skip
// references that fall outside the source array — leave the bare
// `[N]` untouched so we don't produce a broken link.
//
// Conservative regex: only matches `[N]` that's NOT immediately
// followed by `(` (which would be a markdown link already) and is
// preceded by a non-bracket character (so we don't double-process
// `[[1]]` and similar). Anchors require word/punct boundaries on
// both sides so we skip patterns like `[1, 2, 3]` (ranges) where
// the `[` and `]` aren't a single citation.
function linkifyCitations(text: string, urls: string[]): string {
  if (urls.length === 0) return text;
  return text.replace(/(^|[^\[\]\w])\[(\d{1,3})\](?!\()/g, (m, pre, num) => {
    const idx = Number(num) - 1;
    const url = urls[idx];
    if (!url) return m;
    return `${pre}[${num}](${url})`;
  });
}

// Image attachments arrive as `file` UIMessageParts with a data: URL or
// remote URL + IANA mediaType. Filter just the image-typed ones so the
// user message bubble can render thumbnails for them. Non-image files
// (PDFs, txt, etc.) aren't rendered yet — they'd need a different chip.
function imagePartsFromMessage(
  parts: UIMessage["parts"],
): { url: string; mediaType: string }[] {
  const out: { url: string; mediaType: string }[] = [];
  for (const p of parts) {
    if (
      p.type === "file" &&
      typeof (p as { url?: unknown }).url === "string" &&
      typeof (p as { mediaType?: unknown }).mediaType === "string" &&
      String((p as { mediaType: string }).mediaType).startsWith("image/")
    ) {
      out.push({
        url: (p as { url: string }).url,
        mediaType: (p as { mediaType: string }).mediaType,
      });
    }
  }
  return out;
}

type GenImageRender =
  | { key: string; status: "loading"; prompt?: string }
  | { key: string; status: "ready"; url: string; prompt?: string }
  | { key: string; status: "error"; error: string };

// Generated-image tool parts (`tool-generateImage`) → render state. The LIVE
// chat renders these cards from the streamed tool part. On RELOAD the image
// instead arrives as persisted markdown in the assistant text (chat/route.ts
// appends it in onFinish — tool parts aren't persisted), so there's never a
// double render: a live message has the tool part + no markdown; a reloaded
// one has the markdown + no tool part.
function generatedImagesFromMessage(
  parts: UIMessage["parts"],
): GenImageRender[] {
  const out: GenImageRender[] = [];
  for (const p of parts) {
    if (typeof p !== "object" || p === null) continue;
    const obj = p as Record<string, unknown>;
    if (obj.type !== "tool-generateImage") continue;
    const key = (obj.toolCallId as string) ?? `genimg-${out.length}`;
    const state = obj.state as string | undefined;
    const input = (obj.input ?? {}) as Record<string, unknown>;
    const inPrompt =
      typeof input.prompt === "string" ? input.prompt : undefined;
    if (state === "output-available") {
      const output = (obj.output ?? {}) as Record<string, unknown>;
      if (output.status === "ok" && typeof output.url === "string") {
        out.push({
          key,
          status: "ready",
          url: output.url,
          prompt: (output.prompt as string) ?? inPrompt,
        });
      } else {
        out.push({
          key,
          status: "error",
          error: (output.error as string) ?? "Image generation failed.",
        });
      }
    } else if (state === "output-error") {
      out.push({
        key,
        status: "error",
        error: (obj.errorText as string) ?? "Image generation failed.",
      });
    } else {
      // input-streaming | input-available → still generating.
      out.push({ key, status: "loading", prompt: inPrompt });
    }
  }
  return out;
}

// Inline image cards for the in-chat generateImage tool. Styled to match the
// existing attachment-image render + chat design tokens (rounded-2xl, bg-card,
// border-border) — refined minimalism within the existing system.
function GeneratedImageCards({ items }: { items: GenImageRender[] }) {
  if (items.length === 0) return null;
  return (
    <div className="mt-3 flex flex-col gap-3">
      {items.map((it) => {
        if (it.status === "loading") {
          return (
            <div
              key={it.key}
              className="flex aspect-square w-full max-w-sm items-center justify-center rounded-2xl border border-border/60 bg-muted/40"
            >
              <div className="flex flex-col items-center gap-2 text-muted-foreground">
                <ImageIcon className="size-6 animate-pulse" />
                <span className="text-xs">Generating image…</span>
              </div>
            </div>
          );
        }
        if (it.status === "error") {
          return (
            <div
              key={it.key}
              className="flex w-full max-w-sm items-start gap-2 rounded-2xl border border-destructive/30 bg-destructive/5 px-3 py-2.5 text-sm text-destructive"
            >
              <AlertTriangle className="mt-0.5 size-4 shrink-0" />
              <span>{it.error}</span>
            </div>
          );
        }
        return (
          <figure
            key={it.key}
            className="w-full max-w-sm overflow-hidden rounded-2xl border border-border/60 bg-card"
          >
            <a href={it.url} target="_blank" rel="noreferrer" className="block">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={it.url}
                alt={it.prompt ?? "Generated image"}
                className="block w-full object-contain"
              />
            </a>
            <figcaption className="flex items-center justify-between gap-2 px-3 py-2">
              <span
                className="truncate text-xs text-muted-foreground"
                title={it.prompt}
              >
                {it.prompt ?? "Generated image"}
              </span>
              <a
                href={it.url}
                download
                className="shrink-0 rounded-md p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                aria-label="Download image"
                title="Download"
              >
                <Download className="size-3.5" />
              </a>
            </figcaption>
          </figure>
        );
      })}
    </div>
  );
}

// K2.6 Thinking mode emits reasoning via either:
//   1. A custom `data-kimi-reasoning` data part (defensive future-proofing
//      for when we add a custom transform), OR
//   2. The AI SDK 6 native `reasoning` / `reasoning-delta` part type when
//      the openai-compatible provider forwards `reasoning_content`.
// We pick up both so the SDK's native split works on day one without a
// transform, AND the custom path is plumbed for if/when we need it.
function kimiReasoningFromMessage(parts: UIMessage["parts"]): string {
  let text = "";
  for (const p of parts) {
    if (typeof p !== "object" || p === null) continue;
    const obj = p as Record<string, unknown>;
    const t = obj.type as string | undefined;
    if (t === "data-kimi-reasoning") {
      // AI SDK 6 wire format: data-* parts wrap their payload under a
      // `data:` key (strictObject validation rejects flat fields).
      const data = obj.data as { delta?: unknown } | undefined;
      if (typeof data?.delta === "string") text += data.delta;
    } else if (t === "reasoning" || t === "reasoning-delta") {
      // SDK 6 native reasoning part — works for K2.6 if openai-compatible
      // forwards reasoning_content. Fields differ across SDK versions.
      const candidate = obj.text ?? obj.delta ?? obj.reasoning;
      if (typeof candidate === "string") text += candidate;
    }
  }
  return text;
}

// Materialize tool-call/tool-result events into a flat list of entries
// the KimiToolTrace component renders. Two sources of truth:
//   1. Standard SDK 6 `tool-<name>` parts (e.g. `tool-webSearch`) with
//      a `state` field — this is what the AI SDK emits today for Agent
//      mode's webSearchTool.
//   2. Our custom `data-kimi-tool-trace` data parts — forward-compat
//      for adding K2.6's $web_search builtin via a custom transform.
// De-dupes by toolCallId/id so a tool-call followed by tool-result for
// the same call collapses to one entry whose status reflects the
// terminal state.
function toolTraceFromMessage(parts: UIMessage["parts"]): ToolTraceEntry[] {
  const out: ToolTraceEntry[] = [];
  const seenIds = new Set<string>();
  for (const p of parts) {
    if (typeof p !== "object" || p === null) continue;
    const obj = p as Record<string, unknown>;
    const t = obj.type as string | undefined;
    if (!t) continue;

    // Standard SDK shape: type="tool-<name>" with state field.
    if (t.startsWith("tool-")) {
      const toolName = t.slice("tool-".length);
      const id = (obj.toolCallId as string | undefined) ?? `${toolName}-${out.length}`;
      if (seenIds.has(id)) continue;
      seenIds.add(id);
      const state = (obj.state as string | undefined) ?? "input-streaming";
      const input = obj.input as Record<string, unknown> | undefined;
      const output = obj.output as unknown;
      const summary =
        toolName === "webSearch"
          ? ((input?.query as string | undefined) ?? "(query…)")
          : JSON.stringify(input ?? {}).slice(0, 80);
      const status: ToolTraceEntry["status"] =
        state === "output-available"
          ? "ok"
          : state === "output-error"
            ? "error"
            : "pending";
      const resultSummary =
        status === "ok" && output && toolName === "webSearch"
          ? `${(output as { results?: unknown[] }).results?.length ?? 0} results`
          : undefined;
      out.push({ id, toolName, summary, status, resultSummary });
    }

    // Fallback: custom kimi-tool-trace data part (forward-compat
    // for adding $web_search later via a custom transform). AI SDK 6
    // wire format wraps payload under a `data:` key.
    if (t === "data-kimi-tool-trace") {
      const data = (obj.data ?? {}) as Record<string, unknown>;
      const id = (data.id as string | undefined) ?? `custom-${out.length}`;
      if (seenIds.has(id)) continue;
      seenIds.add(id);
      out.push({
        id,
        toolName: (data.toolName as string) ?? "tool",
        summary: (data.summary as string) ?? "",
        status: ((data.status as string) ?? "ok") as ToolTraceEntry["status"],
        resultSummary: data.resultSummary as string | undefined,
      });
    }
  }
  return out;
}

// K2.6 Swarm mode emits `data-kimi-swarm-status` data parts as the
// fan-out completes. The handler currently emits one initial 0/N event
// then one per sub-agent settled (composite-stream prefix). The latest
// event wins — we render a single progress card from the most recent.
function swarmStatusFromMessage(
  parts: UIMessage["parts"],
): { total: number; completed: number; current?: string } | null {
  let last: { total: number; completed: number; current?: string } | null = null;
  for (const p of parts) {
    if (typeof p !== "object" || p === null) continue;
    const obj = p as Record<string, unknown>;
    if (obj.type === "data-kimi-swarm-status") {
      // AI SDK 6 wire format: data-* parts wrap their payload under
      // a `data:` key (strictObject validation rejects flat fields).
      const data = (obj.data ?? {}) as Record<string, unknown>;
      last = {
        total: Number(data.total ?? 0),
        completed: Number(data.completed ?? 0),
        current: data.current as string | undefined,
      };
    }
  }
  return last;
}

export function Message({
  message,
  isStreaming,
  reasoning,
  plan,
  usage,
  error,
  onRetry,
  workspaceId,
  isLast,
}: {
  message: UIMessage;
  isStreaming?: boolean;
  reasoning?: string;
  plan?: { content: string; complete: boolean };
  // Per-turn usage forwarded by the server (input/output token counts +
  // model name). Powers the small chip below the assistant message —
  // visibility into cost like Cursor / OpenRouter / Bolt show.
  usage?: {
    inputTokens: number;
    outputTokens: number;
    reasoningTokens?: number;
    model?: string;
  };
  // When set, the message-actions row gets an Undo button that rolls
  // the workspace back to the snapshot taken just before this turn.
  // Only meaningful for assistant messages in the workbench.
  workspaceId?: string;
  // True for the most-recent message in the thread — the action
  // toolbar is always-visible on the last turn (matches Claude/ChatGPT
  // pattern) and hover-only on earlier turns. Cuts visual noise.
  isLast?: boolean;
  // Error label for failed streams. When set, an inline
  // "Response stopped — Retry" pill renders below the partial text.
  error?: string;
  // Click handler for the retry pill. Receives the failed message id.
  onRetry?: (messageId: string) => void;
}) {
  const isUser = message.role === "user";
  const text = textFromParts(message.parts);
  // Voice mode read-aloud: >= 0 means this message is being spoken; text up to
  // this char index is "read" (white), the rest stays gray. Per-message selector
  // so only the message being read re-renders on each word boundary.
  const voiceReadChar = useVoiceRead((s) => (s.readingId === message.id ? s.readChar : -1));
  const kimiReasoning = kimiReasoningFromMessage(message.parts);
  const toolTrace = toolTraceFromMessage(message.parts);
  const swarmStatus = swarmStatusFromMessage(message.parts);
  const genImages = generatedImagesFromMessage(message.parts);

  // Skip rendering the chat layer's synthetic auto-continue prompt
  // when it leaks into history. New turns no longer persist this
  // (chat/route.ts skips it at saveUserMessage), but legacy DB rows
  // pre-fix still surface as user bubbles in the thread because user
  // messages bypass <Markdown /> and render raw <p>{text}</p>. The
  // canary check is intentionally tight — same as the chat-route
  // skip — so we don't accidentally hide a real user message that
  // happened to start with the same words.
  const trimmed = text.trim();
  const isSyntheticAutoContinue =
    isUser &&
    trimmed.startsWith(
      "Continue your previous output exactly where you stopped",
    ) &&
    trimmed.includes("Close any open boltAction");
  if (isSyntheticAutoContinue) return null;

  // Build seed: when /design's "Build" button auto-fires the workspace
  // chat, the seed prompt (full engineering brief — 30+ lines, ~2KB of
  // text) lands as a user message. Rendered raw it dominates the
  // thread visually and looks like the user typed an essay. Detect
  // the canary phrase and render as a compact "Build started" card
  // with click-to-expand instead. Same canary as in /api/design/build's
  // seed composition — keep these in sync.
  const isBuildSeed =
    isUser &&
    trimmed.includes("MANDATORY: emit `<jarvisplan stages=") &&
    trimmed.includes("ship a real, working full-stack version");
  // Stage progression seed (auto-fired when previous stage verified
  // green). Same idea: collapse so the chat stays readable.
  const isStageProgress =
    isUser && trimmed.startsWith("[auto-progress to stage ");

  // The reasoning block is open by default while reasoning is still
  // streaming (so the user can watch it think live), then auto-collapses
  // once the visible reply starts coming in. This matches Claude.ai's
  // "Thoughts" pattern.
  const reasoningStreaming = Boolean(isStreaming && reasoning && !text);

  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.16, ease: "easeOut" }}
      className={cn("group w-full", isUser ? "flex justify-end" : "flex")}
      // Each turn is a region in the page's accessible tree so SR
      // users can jump between user/assistant messages with regular
      // landmark navigation (NVDA: D, VoiceOver: rotor → Headings).
      role="article"
      aria-label={isUser ? "Your message" : "JARVIS message"}
    >
      {/* Hidden landmark heading — invisible to sighted users but
          gives SR users a "heading-by-heading" jump path through the
          conversation, the same way Claude.ai and ChatGPT mark up
          turns. h2 because the page title is the h1. */}
      <h2 className="sr-only">{isUser ? "You" : "JARVIS"}</h2>
      {isUser && (isBuildSeed || isStageProgress) ? (
        <SystemSeedCard
          text={trimmed}
          kind={isBuildSeed ? "build" : "stage-progress"}
        />
      ) : isUser ? (
        <div className="max-w-[85%] rounded-2xl bg-card px-4 py-2.5 text-foreground space-y-2">
          {(() => {
            const imgs = imagePartsFromMessage(message.parts);
            if (imgs.length === 0) return null;
            return (
              <div className="flex flex-wrap gap-2">
                {imgs.map((img, idx) => (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    key={idx}
                    src={img.url}
                    alt={`attachment-${idx}`}
                    className="block rounded-md max-w-full max-h-64 object-contain border border-border/40"
                  />
                ))}
              </div>
            );
          })()}
          {text && (
            <p className="whitespace-pre-wrap text-[14.5px] leading-6">{text}</p>
          )}
        </div>
      ) : (
        <div className="w-full">
          {kimiReasoning ? (
            <KimiReasoning
              text={kimiReasoning}
              streaming={Boolean(isStreaming && !text)}
            />
          ) : null}
          {toolTrace.length > 0 ? <KimiToolTrace entries={toolTrace} /> : null}
          {swarmStatus ? (
            <KimiSwarmProgress
              total={swarmStatus.total}
              completed={swarmStatus.completed}
              current={swarmStatus.current}
              done={Boolean(text)}
            />
          ) : null}
          {reasoning ? (
            <ReasoningBlock
              reasoning={reasoning}
              streaming={reasoningStreaming}
            />
          ) : null}
          {plan ? (
            <PlanCard
              content={plan.content}
              streaming={!plan.complete && Boolean(isStreaming)}
            />
          ) : null}
          {text ? (
            voiceReadChar >= 0 ? (
              // Voice mode: plain text, revealed gray→white as it's read aloud.
              <p className="whitespace-pre-wrap text-[15px] leading-7">
                <span className="text-foreground">{text.slice(0, voiceReadChar)}</span>
                <span className="text-muted-foreground">{text.slice(voiceReadChar)}</span>
              </p>
            ) : (
              <Markdown
                content={linkifyCitations(
                  text,
                  extractSources(message).map((s) => s.url),
                )}
                isStreaming={isStreaming}
              />
            )
          ) : isStreaming && !reasoning && !plan && genImages.length === 0 ? (
            <ThinkingIndicator />
          ) : null}
          <GeneratedImageCards items={genImages} />
          {/* Polite live region — announces the final answer once the
              stream completes. We deliberately mount the text only when
              !isStreaming so SR doesn't read every token (which would
              be unbearable). The visible Markdown above is aria-hidden
              from this announcement (it's a separate node, but we leave
              it readable by SR users via heading nav as a backup). */}
          {!isUser && text && !isStreaming && (
            <div role="status" aria-live="polite" className="sr-only">
              {text}
            </div>
          )}
          {error && !isStreaming && (
            <ErrorRetryPill
              label={error}
              onRetry={() => onRetry?.(message.id)}
            />
          )}
          {!isStreaming && (() => {
            const sources = extractSources(message);
            return sources.length > 0 ? <Sources sources={sources} /> : null;
          })()}
          {text && !isStreaming && (
            <div
              className={cn(
                "transition-opacity duration-150",
                isLast
                  ? "opacity-100"
                  : "opacity-0 group-hover:opacity-100 focus-within:opacity-100",
              )}
            >
              <MessageActions
                text={text}
                messageId={message.id}
                workspaceId={workspaceId}
                onRegenerate={
                  !isUser && isLast ? () => onRetry?.(message.id) : undefined
                }
              />
            </div>
          )}
          {usage && !isStreaming && <UsageChip usage={usage} />}
        </div>
      )}
    </motion.div>
  );
}

// ── Plan card (Phase 2 of the build workflow) ─────────────────────────────────
//
// The model emits a <jarvisplan> block before the boltArtifact describing
// what it's about to build. We render that as a card above the message
// body so the user has a "blueprint" view before files start streaming.
// Stays expanded by default — the plan is short enough to read in full,
// and matches v0/Bolt's "I'll build…" intro card.
function PlanCard({
  content,
  streaming,
}: {
  content: string;
  streaming: boolean;
}) {
  const [open, setOpen] = useState(true);
  return (
    <div className="mb-3 rounded-lg border border-primary/30 bg-primary/5 overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-[12px] text-foreground/90 hover:bg-primary/10 transition-colors"
      >
        <ListChecks
          className={cn(
            "size-3.5 shrink-0 text-primary",
            streaming && "animate-pulse",
          )}
        />
        <span className="flex-1 font-medium">
          {streaming ? "Planning…" : "Plan"}
        </span>
        <ChevronDown
          className={cn(
            "size-3.5 shrink-0 text-muted-foreground transition-transform duration-200",
            open ? "rotate-180" : "rotate-0",
          )}
        />
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            key="plan-body"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.18, ease: "easeOut" }}
            className="overflow-hidden border-t border-primary/20"
          >
            <div className="px-3 py-2.5 text-[13px] leading-6 text-foreground/90">
              <Markdown content={content || "_planning…_"} />
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

// ── Reasoning block (Claude-style "Thoughts") ─────────────────────────────────

function ReasoningBlock({
  reasoning,
  streaming,
}: {
  reasoning: string;
  streaming: boolean;
}) {
  // Open while the model is still emitting reasoning tokens so the user
  // can watch it think live; collapse once the visible reply takes over.
  const [open, setOpen] = useState(streaming);
  const [duration, setDuration] = useState<number | null>(null);
  const startedAtRef = useRef<number | null>(null);

  useEffect(() => {
    if (streaming && startedAtRef.current === null) {
      startedAtRef.current = Date.now();
    }
    if (!streaming && startedAtRef.current !== null && duration === null) {
      setDuration(Math.max(1, Math.round((Date.now() - startedAtRef.current) / 1000)));
    }
  }, [streaming, duration]);

  // Auto-collapse the moment the visible reply starts streaming. The user
  // can re-expand it if they want to read the trace.
  useEffect(() => {
    if (!streaming) setOpen(false);
  }, [streaming]);

  // Auto-scroll the thinking pane to the latest reasoning while open +
  // streaming, so the user sees the live thought without having to scroll.
  const scrollerRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open || !streaming) return;
    const el = scrollerRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [reasoning, open, streaming]);

  const label = streaming
    ? "Thinking…"
    : duration !== null
      ? `Thought for ${duration}s`
      : "Thoughts";

  return (
    <div className="mb-3 rounded-lg border border-border/40 bg-muted/20 overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-[12px] text-muted-foreground hover:bg-muted/40 transition-colors"
      >
        <Brain
          className={cn(
            "size-3.5 shrink-0",
            streaming ? "text-primary animate-pulse" : "text-muted-foreground/70",
          )}
        />
        <span className="flex-1 font-medium">{label}</span>
        <ChevronDown
          className={cn(
            "size-3.5 shrink-0 transition-transform duration-200",
            open ? "rotate-180" : "rotate-0",
          )}
        />
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            key="reasoning-body"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.18, ease: "easeOut" }}
            className="overflow-hidden border-t border-border/30"
          >
            <div
              ref={scrollerRef}
              className="max-h-64 overflow-y-auto px-3 py-2.5 text-[12px] leading-5 text-muted-foreground/85 whitespace-pre-wrap font-mono"
            >
              {reasoning}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

// ── Thinking indicator ────────────────────────────────────────────────────────

function ThinkingIndicator() {
  return (
    <div className="flex items-center gap-3 py-1">
      {/* JARVIS concentric rings — pulsing with staggered delays */}
      <div className="relative size-8 shrink-0">
        <div className="absolute inset-0 rounded-full border-2 border-primary/25 animate-pulse" />
        <div
          className="absolute inset-1.5 rounded-full border border-primary/50 animate-pulse"
          style={{ animationDelay: "0.35s" }}
        />
        <div
          className="absolute inset-3 rounded-full bg-primary/80 animate-pulse"
          style={{ animationDelay: "0.7s" }}
        />
      </div>
      <ThinkingDots />
    </div>
  );
}

function ThinkingDots() {
  return (
    <span className="flex items-center gap-1" aria-label="Thinking">
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

// ── Streaming cursor ─────────────────────────────────────────────────────

function StreamingCursor() {
  // Blinking caret. Claude.ai / ChatGPT both use a thin vertical bar that
  // appears at the end of the streaming text — bouncing dots are for
  // pre-first-token "Thinking..." state only. The caret is inline so it
  // sits flush after the last rendered character instead of starting a
  // new row of indicators below the text.
  return (
    <span
      aria-hidden
      className="inline-block ml-0.5 align-text-bottom w-0.5 h-[1.1em] bg-foreground animate-pulse"
    />
  );
}

// ── System-generated seed card ──────────────────────────────────────
//
// When the runtime auto-fires a system-generated user message (the
// /design "Build" seed prompt; auto-progress to next stage), the raw
// text is 30+ lines of internal engineering brief. Rendering it as a
// regular user bubble dominates the thread visually and reads as if
// the human typed an essay nobody asked for.
//
// Show a compact card instead — "Building project" / "Stage N
// starting" with a hint and a click-to-expand affordance for power
// users who want to see what was actually fed to the model.
function SystemSeedCard({
  text,
  kind,
}: {
  text: string;
  kind: "build" | "stage-progress";
}) {
  const [open, setOpen] = useState(false);
  const summary =
    kind === "build"
      ? buildSeedSummary(text)
      : stageProgressSummary(text);
  const Icon = kind === "build" ? Hammer : ListChecks;
  return (
    <div className="flex max-w-[85%] flex-col gap-1">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className={cn(
          "flex items-center gap-2 rounded-lg border border-primary/30 bg-primary/5 px-3 py-2",
          "text-left text-[12.5px] hover:bg-primary/10 transition-colors",
        )}
        aria-expanded={open}
      >
        <Icon className="size-3.5 shrink-0 text-primary" />
        <span className="flex-1 truncate text-foreground">{summary.title}</span>
        <span className="shrink-0 text-[11px] text-muted-foreground">
          {summary.subtitle}
        </span>
        <ChevronDown
          className={cn(
            "size-3 shrink-0 text-muted-foreground transition-transform",
            open && "rotate-180",
          )}
        />
      </button>
      {open && (
        <pre className="max-h-80 overflow-y-auto rounded-lg border border-border/40 bg-card/40 p-3 font-mono text-[10.5px] leading-snug text-muted-foreground whitespace-pre-wrap">
          {text}
        </pre>
      )}
    </div>
  );
}

function buildSeedSummary(text: string): { title: string; subtitle: string } {
  // Pull the first sentence's "the X design" → use as title fragment.
  const m = text.match(/Take the (\S+(?:\s+\S+)?) design/);
  const fmt = m ? m[1] : "design";
  const stagesMatch = text.match(/<jarvisplan stages="(\d+)"/);
  const stages = stagesMatch ? `${stagesMatch[1]} stages` : "multi-stage";
  return {
    title: `Building from ${fmt}`,
    subtitle: stages,
  };
}

function stageProgressSummary(text: string): {
  title: string;
  subtitle: string;
} {
  const m = text.match(/\[auto-progress to stage (\d+) of (\d+)\]/);
  if (!m) return { title: "Stage progression", subtitle: "auto" };
  return {
    title: `Stage ${m[1]} of ${m[2]} starting`,
    subtitle: "auto",
  };
}

// ── Error retry pill ─────────────────────────────────────────────────────
//
// Inline "Response stopped — Retry" pill rendered below a partial
// assistant message when the stream errored mid-flight. Pattern from
// Claude.ai / ChatGPT: the partial answer stays visible (don't lose
// the user's tokens), but a low-key warning + one-tap retry sits
// just below it. Click → re-send the original turn from the same
// state. The original message stays in history; the retry produces a
// new assistant message after it.
function ErrorRetryPill({
  label,
  onRetry,
}: {
  label: string;
  onRetry: () => void;
}) {
  return (
    <div
      role="alert"
      className="mt-3 flex flex-wrap items-center gap-2 text-[12.5px] text-muted-foreground"
    >
      <span className="text-destructive/90">{label}</span>
      <button
        type="button"
        onClick={onRetry}
        className={cn(
          "inline-flex items-center gap-1.5 rounded-full",
          "border border-border bg-card/80 px-2.5 py-1",
          "text-foreground hover:bg-card hover:text-foreground",
          "transition-colors",
          "focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/50",
        )}
        aria-label="Retry response"
      >
        <RotateCcw className="size-3" />
        <span>Retry</span>
      </button>
    </div>
  );
}

// ── Usage / cost chip ────────────────────────────────────────────────────────
//
// Per-turn token + dollar cost shown beneath the assistant message.
// Pricing is best-effort and approximate — model providers shift these
// often. We hard-code the common ones; missing entries fall back to
// "—" for cost. Token counts are always shown when available.

const PRICING: Record<string, { inputPer1M: number; outputPer1M: number }> = {
  // Anthropic
  "claude-opus-4-7": { inputPer1M: 15, outputPer1M: 75 },
  "claude-sonnet-4-6": { inputPer1M: 3, outputPer1M: 15 },
  "claude-haiku-4-5": { inputPer1M: 1, outputPer1M: 5 },
  // OpenAI (rough)
  "gpt-5": { inputPer1M: 5, outputPer1M: 20 },
  "gpt-5-mini": { inputPer1M: 0.5, outputPer1M: 2 },
  o3: { inputPer1M: 60, outputPer1M: 240 },
  // Google
  "gemini-2.5-pro": { inputPer1M: 1.25, outputPer1M: 5 },
  "gemini-2.5-flash": { inputPer1M: 0.1, outputPer1M: 0.4 },
  // DeepSeek
  "deepseek-chat": { inputPer1M: 0.14, outputPer1M: 0.28 },
  "deepseek-reasoner": { inputPer1M: 0.55, outputPer1M: 2.19 },
  "deepseek-v4-pro": { inputPer1M: 0.55, outputPer1M: 2.19 },
  "deepseek-v4-flash": { inputPer1M: 0.14, outputPer1M: 0.28 },
};

function formatCost(cost: number): string {
  if (cost === 0) return "free";
  if (cost < 0.001) return "<$0.001";
  if (cost < 0.01) return `$${cost.toFixed(4)}`;
  if (cost < 1) return `$${cost.toFixed(3)}`;
  return `$${cost.toFixed(2)}`;
}

function formatTokens(n: number): string {
  if (n < 1000) return String(n);
  if (n < 10_000) return `${(n / 1000).toFixed(1)}k`;
  return `${Math.round(n / 1000)}k`;
}

function UsageChip({
  usage,
}: {
  usage: {
    inputTokens: number;
    outputTokens: number;
    reasoningTokens?: number;
    model?: string;
  };
}) {
  const { inputTokens, outputTokens, reasoningTokens, model } = usage;
  const pricing = model ? PRICING[model] : undefined;
  const cost = pricing
    ? (inputTokens * pricing.inputPer1M + outputTokens * pricing.outputPer1M) /
      1_000_000
    : null;
  return (
    <div className="mt-1 flex items-center gap-2 text-[10.5px] text-muted-foreground/70 select-none">
      {/* Friendly label (e.g. "Claude Sonnet 4.6") with the raw id on
          hover. This is the model that ACTUALLY ran — on workbench/design
          the server may override the composer's pick for build
          reliability, so this chip is the honest source of truth. */}
      {model && (
        <span className="font-mono" title={model}>
          {MODELS_META[model]?.label ?? model}
        </span>
      )}
      <span>
        {formatTokens(inputTokens)} in · {formatTokens(outputTokens)} out
        {reasoningTokens && reasoningTokens > 0
          ? ` · ${formatTokens(reasoningTokens)} thinking`
          : ""}
      </span>
      {cost !== null && (
        <span title="Approximate cost from public list pricing">
          {formatCost(cost)}
        </span>
      )}
    </div>
  );
}

// ── Message actions ───────────────────────────────────────────────────────────

function MessageActions({
  text,
  messageId,
  workspaceId,
  onRegenerate,
}: {
  text: string;
  messageId?: string;
  workspaceId?: string;
  /** When provided, Regenerate re-runs this turn (re-submits the preceding
   *  user prompt and replaces this assistant message). Omitted on user rows
   *  and on non-final turns. */
  onRegenerate?: () => void;
}) {
  const qc = useQueryClient();
  const [undoing, setUndoing] = useState(false);
  const [copied, setCopied] = useState(false);
  // Thumbs feedback, persisted per-message in localStorage so it survives a
  // refresh without a server round-trip (personal-app scale).
  const fbKey = messageId ? `jarvis:chat:feedback:${messageId}` : null;
  const [feedback, setFeedback] = useState<"up" | "down" | null>(null);
  useEffect(() => {
    if (!fbKey) return;
    const v = window.localStorage.getItem(fbKey);
    if (v === "up" || v === "down") setFeedback(v);
  }, [fbKey]);
  const setFb = (next: "up" | "down") =>
    setFeedback((cur) => {
      const value = cur === next ? null : next;
      if (fbKey) {
        if (value) window.localStorage.setItem(fbKey, value);
        else window.localStorage.removeItem(fbKey);
      }
      return value;
    });
  const onCopy = () => {
    void navigator.clipboard.writeText(text);
    setCopied(true);
    toast.success("Copied to clipboard");
    setTimeout(() => setCopied(false), 1500);
  };
  const onUndo = async () => {
    if (!workspaceId || !messageId || undoing) return;
    if (
      !confirm(
        "Restore the workspace to the state BEFORE this turn? Files added in this turn will be deleted; files modified will revert to their previous content.",
      )
    ) {
      return;
    }
    setUndoing(true);
    try {
      const r = await fetch(
        `/api/workspace/${workspaceId}/checkpoint/restore`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ id: messageId }),
        },
      );
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(j.error ?? `HTTP ${r.status}`);
      }
      const { restored, deleted } = (await r.json()) as {
        restored: number;
        deleted: number;
      };
      toast.success(
        `Reverted: ${restored} file(s) restored, ${deleted} added-this-turn deleted.`,
      );
      qc.invalidateQueries({ queryKey: ["ws", workspaceId, "tree"] });
      qc.invalidateQueries({ queryKey: ["ws", workspaceId, "preview"] });
    } catch (e) {
      toast.error(`Undo failed: ${(e as Error).message}`);
    } finally {
      setUndoing(false);
    }
  };
  return (
    <div className="mt-2 flex items-center gap-0.5">
      <ActionBtn aria-label="Copy" title="Copy" icon={copied ? Check : Copy} onClick={onCopy} />
      <ActionBtn
        aria-label="Good response"
        title="Good response"
        icon={ThumbsUp}
        active={feedback === "up"}
        onClick={() => setFb("up")}
      />
      <ActionBtn
        aria-label="Bad response"
        title="Bad response"
        icon={ThumbsDown}
        active={feedback === "down"}
        onClick={() => setFb("down")}
      />
      {onRegenerate && (
        <ActionBtn
          aria-label="Regenerate"
          title="Regenerate response"
          icon={RotateCcw}
          onClick={onRegenerate}
        />
      )}
      {workspaceId && messageId && (
        <ActionBtn
          aria-label="Undo this turn"
          title="Undo this turn"
          icon={undoing ? Loader2 : Undo2}
          onClick={onUndo}
          spinning={undoing}
        />
      )}
    </div>
  );
}

function ActionBtn({
  icon: Icon,
  onClick,
  spinning,
  active,
  ...props
}: {
  icon: LucideIcon;
  onClick: () => void;
  spinning?: boolean;
  active?: boolean;
  "aria-label": string;
  title?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        "flex size-7 items-center justify-center rounded-md transition-colors",
        active
          ? "text-primary hover:bg-accent/40"
          : "text-muted-foreground/50 hover:bg-accent/40 hover:text-muted-foreground",
      )}
      {...props}
    >
      <Icon className={cn("size-3.5", spinning && "animate-spin")} />
    </button>
  );
}
