"use client";

import { motion } from "motion/react";
import type { UIMessage } from "ai";
import { Copy, ThumbsUp, ThumbsDown, RotateCcw, type LucideIcon } from "lucide-react";
import { Markdown } from "@/components/markdown/markdown";
import { cn } from "@/lib/utils";

function textFromParts(parts: UIMessage["parts"]): string {
  return parts.map((p) => (p.type === "text" ? p.text : "")).join("");
}

export function Message({
  message,
  isStreaming,
}: {
  message: UIMessage;
  isStreaming?: boolean;
}) {
  const isUser = message.role === "user";
  const text = textFromParts(message.parts);

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
          {text ? (
            <>
              <Markdown content={text} />
              {isStreaming && <StreamingCursor />}
            </>
          ) : isStreaming ? (
            <ThinkingIndicator />
          ) : null}
          {text && !isStreaming && <MessageActions text={text} />}
        </div>
      )}
    </motion.div>
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
