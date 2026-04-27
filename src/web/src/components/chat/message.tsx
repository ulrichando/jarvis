"use client";

import { motion } from "motion/react";
import type { UIMessage } from "ai";
import { Copy, Globe, ThumbsUp, ThumbsDown, RotateCcw, type LucideIcon } from "lucide-react";
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
            <Markdown content={text} />
          ) : isStreaming ? (
            <ResearchPanel />
          ) : null}
          {text && !isStreaming && <MessageActions text={text} />}
        </div>
      )}
    </motion.div>
  );
}

// ── Streaming / research panel ────────────────────────────────────────────────

const SHIMMER_ROWS = [
  [52, 28, 64],   // widths (%) for each row's title + spacer + domain
  [44, 28, 72],
  [58, 28, 56],
] as const;

function ShimmerRow({ widths }: { widths: readonly [number, number, number] }) {
  return (
    <div className="flex items-center gap-2 px-3 py-1.75">
      <div className="size-3.5 shrink-0 rounded-sm bg-muted-foreground/12" />
      <div
        className="h-2.25 rounded-full bg-muted-foreground/12 animate-pulse"
        style={{ width: `${widths[0]}%` }}
      />
      <div className="flex-1" />
      <div
        className="h-2.25 shrink-0 rounded-full bg-muted-foreground/8 animate-pulse"
        style={{ width: `${widths[2]}px` }}
      />
    </div>
  );
}

function SearchCard({ queryWidth, delay }: { queryWidth: number; delay: string }) {
  return (
    <div className="overflow-hidden rounded-xl border border-border/40 bg-card/30">
      <div className="flex items-center justify-between px-3 py-2">
        <div className="flex items-center gap-2">
          <Globe className="size-3.5 shrink-0 text-muted-foreground/50" />
          <div
            className="h-2.25 rounded-full bg-muted-foreground/15 animate-pulse"
            style={{ width: `${queryWidth}px`, animationDelay: delay }}
          />
        </div>
        <div
          className="h-2.25 w-14 shrink-0 rounded-full bg-muted-foreground/10 animate-pulse"
          style={{ animationDelay: delay }}
        />
      </div>
      <div className="max-h-28 divide-y divide-border/25 overflow-y-auto border-t border-border/30">
        {SHIMMER_ROWS.map((w, i) => (
          <ShimmerRow key={i} widths={w} />
        ))}
      </div>
    </div>
  );
}

function ResearchPanel() {
  return (
    <div className="space-y-3 max-w-2xl">
      <div className="flex items-center gap-2">
        <span
          className="inline-block text-[18px] leading-none text-primary"
          style={{ animation: "spin 3s linear infinite" }}
        >
          {"✻"}
        </span>
        <span className="text-[14px] text-foreground/75">Searching sources…</span>
      </div>
      <div className="space-y-2.5 pl-1">
        <SearchCard queryWidth={220} delay="0ms" />
        <SearchCard queryWidth={180} delay="150ms" />
      </div>
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
