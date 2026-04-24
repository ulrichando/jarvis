"use client";

import { motion } from "motion/react";
import type { UIMessage } from "ai";
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
        <div className="max-w-[85%] rounded-2xl rounded-br-sm border border-primary/30 bg-primary/12 px-4 py-2.5 text-foreground shadow-[inset_0_1px_0_0_oklch(1_0_0_/6%)]">
          <p className="whitespace-pre-wrap text-[14.5px] leading-6">{text}</p>
        </div>
      ) : (
        <div className="flex w-full gap-3">
          <div className="shrink-0 flex size-7 items-center justify-center rounded-md border border-primary/40 bg-primary/10 font-mono text-[10px] font-semibold uppercase tracking-wider text-primary">
            J
          </div>
          <div className="min-w-0 flex-1 pt-0.5">
            {text ? (
              <Markdown content={text} />
            ) : isStreaming ? (
              <ThinkingDots />
            ) : null}
          </div>
        </div>
      )}
    </motion.div>
  );
}

function ThinkingDots() {
  return (
    <div className="flex items-center gap-1.5 py-2">
      <span className="size-1.5 animate-pulse rounded-full bg-primary [animation-delay:-0.3s]" />
      <span className="size-1.5 animate-pulse rounded-full bg-primary [animation-delay:-0.15s]" />
      <span className="size-1.5 animate-pulse rounded-full bg-primary" />
    </div>
  );
}
