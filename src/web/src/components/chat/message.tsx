"use client";

import { useEffect, useRef, useState } from "react";
import { motion, AnimatePresence } from "motion/react";
import type { UIMessage } from "ai";
import {
  Copy,
  ThumbsUp,
  ThumbsDown,
  RotateCcw,
  ChevronDown,
  Brain,
  ListChecks,
  Undo2,
  Loader2,
  type LucideIcon,
} from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Markdown } from "@/components/markdown/markdown";
import { cn } from "@/lib/utils";

function textFromParts(parts: UIMessage["parts"]): string {
  return parts.map((p) => (p.type === "text" ? p.text : "")).join("");
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

export function Message({
  message,
  isStreaming,
  reasoning,
  plan,
  usage,
  workspaceId,
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
}) {
  const isUser = message.role === "user";
  const text = textFromParts(message.parts);

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
    >
      {isUser ? (
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
            <>
              <Markdown content={text} />
              {isStreaming && <StreamingCursor />}
            </>
          ) : isStreaming && !reasoning && !plan ? (
            <ThinkingIndicator />
          ) : null}
          {text && !isStreaming && (
            <MessageActions
              text={text}
              messageId={message.id}
              workspaceId={workspaceId}
            />
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
  return (
    <div className="mt-3 flex items-center gap-1" aria-hidden>
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className="size-1.5 rounded-full bg-primary/60 animate-bounce"
          style={{ animationDelay: `${i * 0.18}s`, animationDuration: "1s" }}
        />
      ))}
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
  // Groq (free-tier, but shown for awareness)
  "llama-3.3-70b": { inputPer1M: 0, outputPer1M: 0 },
  "gpt-oss-120b": { inputPer1M: 0, outputPer1M: 0 },
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
      {model && <span className="font-mono">{model}</span>}
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
}: {
  text: string;
  messageId?: string;
  workspaceId?: string;
}) {
  const qc = useQueryClient();
  const [undoing, setUndoing] = useState(false);
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
      <ActionBtn aria-label="Copy" icon={Copy} onClick={() => navigator.clipboard.writeText(text)} />
      <ActionBtn aria-label="Like" icon={ThumbsUp} onClick={() => {}} />
      <ActionBtn aria-label="Dislike" icon={ThumbsDown} onClick={() => {}} />
      <ActionBtn aria-label="Regenerate" icon={RotateCcw} onClick={() => {}} />
      {workspaceId && messageId && (
        <ActionBtn
          aria-label="Undo this turn"
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
  ...props
}: {
  icon: LucideIcon;
  onClick: () => void;
  spinning?: boolean;
  "aria-label": string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex size-7 items-center justify-center rounded-md text-muted-foreground/50 hover:bg-accent/40 hover:text-muted-foreground transition-colors"
      {...props}
    >
      <Icon className={cn("size-3.5", spinning && "animate-spin")} />
    </button>
  );
}
