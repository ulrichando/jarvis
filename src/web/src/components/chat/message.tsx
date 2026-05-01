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
  type LucideIcon,
} from "lucide-react";
import { Markdown } from "@/components/markdown/markdown";
import { cn } from "@/lib/utils";

function textFromParts(parts: UIMessage["parts"]): string {
  return parts.map((p) => (p.type === "text" ? p.text : "")).join("");
}

export function Message({
  message,
  isStreaming,
  reasoning,
  plan,
}: {
  message: UIMessage;
  isStreaming?: boolean;
  reasoning?: string;
  plan?: { content: string; complete: boolean };
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
        <div className="max-w-[85%] rounded-2xl bg-card px-4 py-2.5 text-foreground">
          <p className="whitespace-pre-wrap text-[14.5px] leading-6">{text}</p>
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
          {text && !isStreaming && <MessageActions text={text} />}
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

// ── Message actions ───────────────────────────────────────────────────────────

function MessageActions({ text }: { text: string }) {
  return (
    <div className="mt-2 flex items-center gap-0.5">
      <ActionBtn aria-label="Copy" icon={Copy} onClick={() => navigator.clipboard.writeText(text)} />
      <ActionBtn aria-label="Like" icon={ThumbsUp} onClick={() => {}} />
      <ActionBtn aria-label="Dislike" icon={ThumbsDown} onClick={() => {}} />
      <ActionBtn aria-label="Regenerate" icon={RotateCcw} onClick={() => {}} />
    </div>
  );
}

function ActionBtn({
  icon: Icon,
  onClick,
  ...props
}: {
  icon: LucideIcon;
  onClick: () => void;
  "aria-label": string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex size-7 items-center justify-center rounded-md text-muted-foreground/50 hover:bg-accent/40 hover:text-muted-foreground transition-colors"
      {...props}
    >
      <Icon className="size-3.5" />
    </button>
  );
}
