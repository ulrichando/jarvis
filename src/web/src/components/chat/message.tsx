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
            <Markdown content={text} />
          ) : isStreaming ? (
            <StreamingSpark />
          ) : null}
          {text && !isStreaming && <MessageActions text={text} />}
        </div>
      )}
    </motion.div>
  );
}

function StreamingSpark() {
  return (
    <div className="flex items-center gap-2">
      <span className="animate-spin text-[18px] leading-none text-primary" style={{ animationDuration: "3s" }}>
        {"✻"}
      </span>
      <span className="text-[14px] text-muted-foreground">Thinking</span>
    </div>
  );
}

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
